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
  namespace. `check_definition` in `delivery_state.py` digests one entry. That digest covers its argv, cwd,
  prerequisites, timeout, profile environment and the binding's `prepare` steps. Lane `gates` in
  `.agents/run-delivery.json` are a separate list of argv entries with no ids in common with any of them.
- **One way of running.** The queue runs each check in a fresh worktree of the commit (`Runner.checkout`), under
  `workRoot/checkouts`, which `Runner.start` clears at the start of every queue command. The worktree is prepared by
  the binding's `prepare` steps. The environment `Runner.environment` builds is the queue process's whole environment
  minus git's location variables, plus the binding's `environment`, the check's profile, a fixed git identity and the
  hooks directory. The same function also serves the runner's own git and its merge, ready, release, postSync and
  fullHealth steps. A lane runs its checks in its own working checkout, with whatever environment its session has.
- **Two reuse tests in `merge_queue.py`.**
  - `reusable` accepts a lane's hand-off evidence only at the exact same tree, and only for ids in `reusableChecks`,
    which may name `alwaysRun` and `fallback` checks only.
  - `carry_plan` keeps the queue's own earlier pass across a base move that changes nothing the check reads. It uses
    `selection.readers` entries marked `complete`, plus `selection.unread`.
- **No lane records.** run-delivery never writes the hand-off evidence that `reusable` needs. Hand-off evidence has
  exactly five fields (`EVIDENCE_FIELDS`: `check`, `definition`, `head`, `tree`, `result`), checked by
  `evidence_errors` and by `item.evidence` in `schemas.json`. `gates.json` records the gate run at HEAD, and
  `gates_summary` requires its result ids to equal the configured gate ids. `handoff --checks` lists ids the lane
  says it ran, which is a claim, not a record.
- **Guards.** `selection.guards` (`tests.test_linear_qualification`, `tests.test_registry`) are added to every lead
  selection, so they run in the queue only. Their measured medians in the V26-3606 record are 29 s and 23 s.
- **Cost.** `meta.checkHistory` keeps each check's last five passing durations. Nothing outside the queue's progress
  view reads them.

This repository's `durable-text` lane gate is a stopgap for the missing guard run. Its `catalogue` gate runs the
3-second `StandingBinding` class, so a lane meets the catalogue check before the queue does. A pin in
`tests/test_own_deliveries.py` (`StandingBinding.test_every_lane_gate_runs_the_catalogue_and_durable_text_guards`)
holds both in place. No `selection.readers` entry here is `complete`, and the binding sets no `selection.unread`.

The paired TypeScript harness has no queue, carry or reuse code. A search of `product-runtime/` and
`agent-delivery-harness/packages` finds none. This plan changes Python only.

## What a reader should be able to do

- A lane runs any check by id with one command. The command runs it the way the queue does and records the run. The
  hand-off carries the records.
- The queue reuses a recorded pass whenever it can show that its own run would see the same inputs: the same
  definition, the same content on every path the check reads, the same declared environment and the same host
  tools. Otherwise it journals what differs.
- A lead sees, before accepting, which checks have a pass that applies at the handed head, which don't and why, and
  how long each takes. Where the paths the base brought in can't be proven unread, the lead can grant reuse with a
  reason, and the grant is recorded.
- A lane meets the binding's guards at `gate`, before review. A lead push can't publish a base that a guard fails
  unless the lead records why.

## Finish line

1. In this repository, a lane's recorded pass of a catalogue check, made with `run_delivery.py check` in the lane's
   worktree, is reused by a queue run from the lead's checkout at an identical tree, and journaled as `check.reused`.
   Reuse across a base move that is wholly unread, or that touches nothing in a known read set, is shown by
   Scenarios 9 and 10 in `tests/test_merge_queue.py`, since this repository declares no complete read sets.
2. A lane record made with a different declared environment, different host tools, in the lane's own checkout, or
   with no environment or host facts, isn't reused. The queue's note names what differs: the variable names, the tool
   names, the platform, the checkout, or the missing facts.
3. A V26-3614-shaped candidate, whose base merge brought in only paths outside its own diff, reuses its seven passes
   when the lead accepts it with a reuse grant and a reason. The reuse is journaled as `check.reused`, with `because`
   naming the lead's `decision.recorded` entry.
4. `gate` runs the binding's guards first by default, and this repository's stopgap `durable-text` gate is gone.
5. Apart from Decisions 4 and 13, settings that set none of the new fields behave as before. Every existing test
   passes unchanged, apart from the pins listed under Stories.
6. The full suite passes on macOS with Python 3.11 within `tests/suite-budget.json`.

## Decisions

1. **Primitives, defaults and recorded overrides, with no prescribed rules.** Each gap is closed by a record, an
   answer or a measurement that agents read where they decide. Commands do the common thing by default. An agent
   steps off a default with `--why`, which records its reason. A `--skip-guards` without `--why` is a usage error. A
   command refuses only to protect the shared base, which is Decision 7's `lead-push` refusal. Every story's criteria
   include that no fixed rule replaces the agent's judgement.
2. **One run record.** A run record has these fields:
   - `check` and `definition`, the `check_definition` digest from the binding in that head's tree;
   - `head` and `tree`;
   - `result`, `seconds` and `at`;
   - `where`: `runner` for a run by `run-checks` or the queue, or `checkout` for a run in the lane's own checkout;
   - `environment`: `{names, digest}`, the names the check received and a sha256 over the digested values
     (Decision 13 says which);
   - `host`: `{platform, tools}`, where `platform` is the system, release and machine, and `tools` maps each tool
     Decision 12 names to the sha256 of its resolved executable.

   Records hold no variable's value: only names and the digest. `run-checks` writes records, and the queue writes the
   same fields on its rows. A record can also reach the queue through `delivery_state.py handoff --evidence`, which
   takes JSON. That path is bounded, not closed: `applies` re-derives the definition, tree and head from git and the
   binding, and compares the facts with the queue's own. So a hand-typed record can claim only a pass whose inputs all
   match the queue's run. It can't claim a pass under another definition, tree or environment. `fullHealth` after
   every merge remains the backstop for a claimed run that never happened.
3. **One applicability answer.** `applies(record, check, tree, binding, facts)` in `merge_queue.py` returns applies,
   or every reason it doesn't. `facts` are the environment and host the queue's own run of that check would have. A
   record applies at `tree` when all of these hold:
   - its result is `passed`;
   - its `where` is `runner`;
   - its definition digest equals the check's digest under `binding`;
   - it has `environment` and `host`, and both equal `facts`;
   - its head is present, with the record's tree;
   - the paths that differ between the record's tree and `tree` (`git diff --name-only --no-renames`) are empty,
     **or** all match `selection.unread`, **or** none is in the check's known read set.

   The binding file always counts as read. A read set is known only when every `readers` entry naming the check is
   `complete`, as today. A record missing `environment` or `host` doesn't apply, and the reason says the facts are
   missing.

   `reusable` and `carry_plan`'s per-check test both call `applies`. `carry_plan` builds its record from the queue's
   row and the synced head that row was checked at. For a row written before this change, which has no `environment`
   or `host`, `carry_plan` supplies the queue's current facts, so such a row carries as it does today. Only
   `carry_plan` does this, and only for its own rows.

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
5. **A lead grant covers the paths the base brought in (operator, 2026-11-19).** `lead-accept --reuse <ids> --why
   <text>` records the grant in the candidate's `leadSelection` at the handed head, and journals `decision.recorded`.
   - The grant lives in `leadSelection`, so it is void at any other handed head, as a lead selection already is. A new
     hand-off needs a new grant.
   - A grant may name only a check the hand-off carries a record for. `select` rejects any other id as invalid input.
   - For a granted id, `applies` compares paths as usual, minus one exemption. The candidate's own paths are what it
     changes against the base, the notion `carry_plan` already uses, taken at both ends and joined: `git diff
     --name-only --no-renames $(git merge-base <base head> <record head>) <record head>` and `git diff --name-only
     --no-renames $(git merge-base <base head> <handed head>) <handed head>`. A path that differs between the record's
     tree and the handed tree, and is outside both, came from the base (through the lane's own base merge or the
     queue's sync), and is treated as unread. A differing path inside either one is one the candidate touched, and
     counts as usual. So with an unknown read set, any lane change after the record to a path the candidate changes,
     including a revert of one it had changed, makes the check run, while a base merge alone doesn't.
   - Every other condition in Decision 3 still holds. A granted record made in the lane's own checkout, under another
     definition, environment or host, with missing facts, or that failed, still runs.
   - When the queue reuses a granted record, `check.reused`'s `because` names the decision.
6. **Guards run first at `gate` by default (operator, 2026-11-19).** `gate` runs `selection.guards` through
   `run-checks` (Decision 12), before the repository's gates. It prints each guard's measured median and the prepare
   time, and stops at the first failure.
   - A guard with a record that applies at the same tree isn't run again.
   - `gate --skip-guards --why <text>` skips them.
   - Guard records and any skip reason are kept under their own `guards` key in `gates.json`. `results` keeps holding
     exactly the configured gates, so `gates_summary`'s check is unchanged.
   - `handoff` carries a skip reason into the hand-off, so the lead sees it at acceptance.
   - `handoff`, `review-prepare` and `lead-accept` show each guard's record at the head. They don't run guards
     themselves.
7. **`lead-push` runs guards against what it publishes.** It runs them through `run-checks` at the commit it would
   publish, because the lead's own checkout isn't assumed to be prepared. It refuses on a failure, naming the guard,
   which protects the shared base. `--skip-guards --why <text>` publishes anyway and journals `decision.recorded`, for
   the case of a lead pushing a fix to a broken guard itself.
8. **Cost is measured, not declared.** No `cost` field is added, and the guard ceiling V26-3428 proposed is dropped.
   Commands show `meta.checkHistory` medians where a choice is made.
9. **Lane gates may name a check by id.** A `gates` entry in `run-delivery.json` may be a string naming a catalogue,
   `alwaysRun` or `fallback` id, which runs through `run-checks` from that definition. Inline entries run in the
   lane's checkout as today, and their records say `where: checkout`.
   - `doctor` reports an unknown id.
   - `doctor` resolves a program by path only when that path is tracked or on `PATH`. An untracked relative program,
     such as `.venv/bin/python`, is reported as made by `prepare` rather than lost, so a fresh clone still passes
     `doctor`.
10. **No new journal kinds.** Reuse uses `check.reused` and `check.carried` as they are, and overrides use
    `decision.recorded`. The hand-off facts gain optional fields (CHK-1), and the existing five-field vectors in
    `tests/fixtures/journal-kinds/vectors.json` stay valid unchanged.
11. **Plan review has no round bound.** It runs until its lenses align, or until the operator ends it.
12. **A reusable run is one the queue would reproduce.** `merge_queue.py run-checks --repo <checkout> --head
    <commit> --checks <ids>` runs checks the way the queue does. It runs `prepare`, builds each check's environment as
    Decision 13 says, and runs each check with the queue's own code. It prints one record per id (Decision 2) with
    `where: runner`, and writes no delivery state.
    - **Its own worktree.** It makes its worktree in its own directory, `workRoot/runs/<pid>-<n>`. It never calls
      `Runner.start` and never touches `workRoot/checkouts`, and it removes only what it made, so it can run while the
      queue serves from the same checkout.
    - **Host facts.** `platform` is the system, release and machine. `tools` are argv[0] of the check and of each
      `prepare` step, plus any names in `reuse.tools`, each resolved on the check's `PATH` with links followed. This
      repository lists `git`, `node` and `npm`.
    - **When facts are computed.** The queue computes host facts once per run, after `prepare`. It computes each
      check's environment digest per check, since a profile changes it.
    - **No more `evidence`.** `merge_queue.py evidence` is removed. `run-checks` is how a lane gets a record.
13. **A check's environment is declared, not inherited.** This covers the argv of checks and of `prepare` steps, in
    the queue and in `run-checks`. The runner's own git, and its merge, ready, release, postSync and fullHealth steps,
    keep today's inherited environment through `Runner.environment`.

    A check's environment is built from these sources only:
    - a fixed baseline of inherited names: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, and every
      `LC_*` name;
    - the names the binding lists in `inherit`;
    - the binding's `environment`, the profile, the git identity and the hooks directory, as today.

    The digest is a sha256 over the sorted `name=value` pairs of `LANG`, every `LC_*`, every `inherit` name, the
    binding's `environment` and the profile, so two names swapping values changes it. Values that depend on where or by whom the run happens are passed to the check but
    left out of the digest: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL` and `TMPDIR`, plus the runner-owned git identity
    and hooks directory, and anything derived from `--repo` or `workRoot`. `PATH`'s effect is captured by the tool
    digests instead.

    Everything else in the process environment is dropped. A check that needed a dropped variable fails until the
    binding lists it in `inherit`, and its failure output names the dropped variables. This repository lists none to
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
   the environment names and digest, and the host facts. A failure is recorded with its log, and the command exits
   non-zero. A variable planted with a sentinel value is absent from the record and from any failure output
   (CHK-1).
2. **Declared environment.** A variable outside the baseline and `inherit` doesn't reach a check, in the queue or in
   `run-checks`. A check that fails without it has the dropped names in its failure output, and listing the name in
   `inherit` makes it pass. A baseline name, `LC_ALL`, reaches the check. The queue's own fetch and merge still get
   the inherited environment, so a variable only the runner's git needs isn't dropped from it (CHK-1).
3. **Same facts across checkouts.** `run-checks` from a second worktree of the same repository, in a child process
   whose `TMPDIR` and `PATH` values differ from the queue's, produces the same environment digest and host facts as
   the queue does from the main checkout. `PATH` differs there only in entries that resolve no named tool differently
   (CHK-1).
4. **Beside a serving queue.** `run-checks` runs from the queue's own `--repo`. A test seam holds a queue command
   just after `Runner.start` has cleared `workRoot/checkouts`, while `run-checks` is live, so the overlap is certain
   rather than timed. Both finish, and neither removes the other's worktree (CHK-1).
5. **Widened hand-off evidence.** `handoff --evidence` accepts a record with `where`, `environment`, `host`,
   `seconds` and `at`, keeps them through `handoff.recorded` and replay, and still accepts the five-field shape
   (CHK-1).
6. **Characterization.** Every existing `reusable` and `carry_plan` outcome in `tests/test_merge_queue.py` holds after
   both call `applies`. A queue row written before this change, with no `environment` or `host`, still carries
   (CHK-2).
7. **Lane record, identical tree.** The record applies, and the queue journals `check.reused` (CHK-2).
8. **Missing facts.** A lane record with no `environment`, or with no `host`, runs, and the note says the facts are
   missing (CHK-2).
9. **Lane record across an unread base move.** The base merge changes only paths matching `selection.unread`, and the
   record applies (CHK-2).
10. **Lane record across a move outside the read set.** The check has a complete read set and the move touches none
    of it, so the record applies (CHK-2).
11. **A move inside the read set, or an unknown read set with differing trees.** The check runs. The note names the
    path, or says the read set is unknown (CHK-2).
12. **Facts differ.** One record differs from the queue's facts in an environment value, one in two variables
    swapping values, one in a tool digest, one in the platform, and one has `where: checkout`. Each runs, and the note names the differing variable names, the tool
    names, the platform or the checkout. It never prints a value (CHK-2).
13. **Other reasons.** A changed definition, a failed record, a prerequisite of another selected check, a binding
    file change, an excluded check (with its `why` quoted) and a `{baseHead}` placeholder each run with their own
    reason. A binding that still sets `reusableChecks` is refused, naming `reuse.exclude` (CHK-2).
14. **Grant.** Each case is one row (CHK-2 for the queue side, CHK-4 for `lead-accept`):
    - the V26-3614 shape: the record's head is the lane head before a lane-side base merge, the handed head is that
      merge commit, the merge brought in only paths outside the candidate's diff against the base, and the read set is
      unknown. With `--reuse` and `--why` the record applies, and `check.reused`'s `because` names the
      `decision.recorded` entry;
    - the same shape, plus a lane commit after the record that touches a path inside the candidate's diff against the
      base: the granted check runs, and the note names that path;
    - the same shape, plus a lane commit after the record that reverts a path the candidate had changed to the base's
      content: the granted check runs, and the note names that path;
    - a grant recorded at an earlier handed head doesn't apply after a new hand-off;
    - a granted record with `where: checkout`, different facts, a changed definition or a failed result still runs.
15. **A lane runs checks by id.** `check <key> <ids>` calls `run-checks` at HEAD and keeps the records in the story's
    review folder (CHK-3).
16. **End to end, lane to queue.** In a child process whose `TMPDIR` and `PATH` differ from the queue's, a lane runs
    `run_delivery.py check` in its worktree and hands off. A queue run from the main checkout at the same tree then
    reuses the record and journals `check.reused` (CHK-3).
17. **Hand-off evidence.** For each reusable check, `handoff` attaches the latest record, whatever its result, at the
    handed head or its nearest ancestor, so a later failure replaces an earlier pass. It shows each `--checks` id as
    recorded, with its head and result, or as named only, and refuses neither (CHK-3).
18. **Gate by id.** A `gates` entry `"validate-registry"` runs through `run-checks` and leaves a record, and an inline
    gate leaves a `where: checkout` record. `doctor` reports an unknown id, and a fresh clone with no `.venv` passes
    `doctor` (CHK-3).
19. **Guards at gate.** A diff that `tests.test_linear_qualification` rejects fails `gate`, naming the guard, before
    the repository's gates run. At the same tree, a second `gate` reuses the guard pass. `--skip-guards --why`
    records the skip and its reason under `guards`, the hand-off shows them, and `gates_summary` still passes.
    `--skip-guards` without `--why` is a usage error (CHK-4).
20. **`lead-push`.** Each case is one row (CHK-4):
    - with guards configured and passing at the commit it publishes, `lead-push` publishes with no `--skip-guards`
      and journals no `decision.recorded`;
    - with a lead HEAD that is a merge whose result a guard rejects, though each parent passes, `lead-push` refuses,
      and nothing is published;
    - with `--skip-guards --why` it publishes, and `decision.recorded` is journaled.
21. **What the lead sees.** `lead-accept`'s proposal shows, for each selected check, applies or why not, plus its
    measured median (CHK-4).
22. **Order.** In a queue run, guards start first, then the rest longest first by median, with checks that have no
    history after those that do. A failing guard ends the run before any other check starts. A passing run records
    the same results as before (CHK-5).
23. **Nothing new set.** Settings with no `reuse.exclude`, no `inherit`, no guards and no id gates gate and carry as
    before. The only changes are Decisions 4 and 13 (every story).

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | CHK-1, CHK-2, CHK-5 | `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `tests/test_merge_queue.py` and `tests/test_orchestration_state.py` |
| 1 | 2 | CHK-3 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md` and `tests/test_run_delivery.py` |
| 2 | 2 | CHK-4 | `.agents/run-delivery.json`, `.agents/orchestration.json` and `tests/test_own_deliveries.py` |

`provenance.lock.json` is shared, and the provenance writer resolves it. CHK-3 starts once CHK-1's `run-checks` and
widened evidence have merged, and runs alongside CHK-2. CHK-3's end-to-end case lives in `tests/test_run_delivery.py`
and drives the queue as a child process, so it owns no file of lane 1's. CHK-4 needs `applies` from CHK-2 and the records
from CHK-3.

## Stories

### CHK-1. Run a check the way the queue does, anywhere, with the facts that make it reproducible

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_merge_queue.py` and
`tests/test_orchestration_state.py`.

- Declared environment for checks and `prepare` steps, with `inherit` and the digest Decision 13 describes. The
  runner's own steps keep `Runner.environment`.
- `run-checks` in its own `workRoot/runs/` directory, with records and host facts as Decisions 2 and 12 describe, and
  `reuse.tools`. `evidence` is removed.
- Queue rows gain `environment` and `host`.
- Widen the hand-off evidence. `EVIDENCE_FIELDS`, `evidence_errors`, `item.evidence` in `schemas.json`, and the
  `handoff.recorded` facts and replay gain optional `where`, `environment`, `host`, `seconds` and `at`. The five-field
  shape stays valid.
- Pins: the `evidence` command cases in `tests/test_merge_queue.py`, and the evidence-shape cases in
  `tests/test_orchestration_state.py` (around its lines 4229-4238 and 6342). Both move to `run-checks` and the widened
  shape. `delivery_state.py`'s mention of `evidence` names `run-checks`.
- No fixed rule replaces the agent's judgement. The facts are recorded, and nothing is refused on them here.

### CHK-2. Answer whether a recorded pass applies, and reuse a lane's pass by default

Lane 1, tier 1, after CHK-1. Characterization-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`,
`tests/test_merge_queue.py` and `tests/test_orchestration_state.py`.

- `applies` as Decision 3 describes. `reusable` and `carry_plan`'s per-check test call it, each keeps its outer
  conditions, and only `carry_plan` supplies facts for its own older rows.
- Reuse by default, `reuse.exclude`, and `reusableChecks` refused (Decision 4). Update the schema, the guide's binding
  table, and the guide's lane hand-off section, which today tells lanes to paste `evidence` output.
- `select --reuse <ids> --why <text>` records the grant in `leadSelection` and journals `decision.recorded`.
  `applies` honours it with the own-diff bound (Decision 5).
- Pins: the `reusableChecks` cases in `tests/test_merge_queue.py` become `applies` reasons, plus one refusal.
- No fixed rule replaces the agent's judgement. The queue's default is reuse when `applies` says so, and the grant is
  the recorded override.

### CHK-3. Record a lane's check runs, and hand off with them

Lane 2, tier 1, after CHK-1. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md` and `tests/test_run_delivery.py`.

- `check <key> <ids>` calls `run-checks` at HEAD and keeps the records in the story's review folder. It prints each
  check's median and the prepare time.
- `gate` keeps a record for each gate inside `gates.json` `results`: `runner` for a gate named by id, `checkout` for
  an inline one (Decision 9). `doctor` resolves gates named by id.
- `handoff` attaches records as Scenario 17 describes, in the widened evidence shape from CHK-1.
- Scenario 16's end-to-end case, in `tests/test_run_delivery.py`, running the queue as a child process.
- No fixed rule replaces the agent's judgement. Nothing is refused for a missing record.

### CHK-4. Run guards where a change is checked, and show the lead what still applies

Lane 2, tier 2, after CHK-2 and CHK-3. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery.py`,
`.agents/run-delivery.json`, `.agents/orchestration.json` and `tests/test_own_deliveries.py`.

- Guards at `gate` (Decision 6) and at `lead-push` (Decision 7). `handoff`, `review-prepare` and `lead-accept` show
  each guard's record at the head.
- `lead-accept --reuse <ids> --why <text>` passes the grant to `select`, and its proposal shows Scenario 21's facts.
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
  come before the checks that need them, and a failing guard ends the run (Scenario 22).
- No fixed rule replaces the agent's judgement. Ordering changes nothing about which checks run.
