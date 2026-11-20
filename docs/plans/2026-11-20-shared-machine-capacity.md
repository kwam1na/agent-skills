---
reviewSlug: shared-machine-capacity
date: 2026-11-20
execution_posture: test-first
context: A delivery's heavy runs share one machine, but no agent can see what the others are running, what a run will cost, or what the machine can bear. This plan adds three primitives (a claims ledger, a measured cost record and a capacity setting) and puts defaults on top of them. Heavy commands claim by default and wait when the machine is full. Any agent can step off a default with a reason, and the reason is recorded. Nothing refuses except to protect shared state.
---

# Shared-machine capacity: claims, costs and capacity

The ticket is [V26-3638](https://linear.app/v26-technologies/issue/V26-3638). This plan is its outcome. Its
stories are filed under V26-3638 once the plan is approved, and their keys replace the placeholder keys `MC-1` to
`MC-4` used below.

## The problem, as the code and the machine have it

On 2026-11-18, during V26-3606, the lead's wait reported a five-minute load of 20.9 against its limit of 20 (twice
the 10 cores). Four runs overlapped: a lane's hand-off full suite (`--workers 4`), two reviewers' mutation runs, and
V26-3630's queue landing on the same base.

These are the parts the code already has:

- **Pressure is measured, but only to wake the lead.** `machine_reading` in `delivery_state.py` reads `cores`,
  `load5`, `diskFreeBytes` and `swapUsedBytes`. `wait_for_lead` wakes when load passes `--load-multiple` (2.0) times
  the cores, when free disk drops below `--min-free-bytes` (20 GiB), or when swap grows past `--swap-growth-bytes`
  (4 GiB). No other command reads these numbers.
- **The merge slot serializes base changes, not machine use.** `merge_slot` keeps one lock per base, at
  `<git common dir>/agent-skills/merge-slot/<base>.lock`, with holder and waiter files. Liveness comes from the
  lock, which the operating system releases when its holder dies, not from the pid. The queue (`Runner`, around
  `merge_queue.py:3853`) and the lead's push, sync, upgrade, check, merge and release commands hold it.
- **The queue runs checks inside the slot.** `Runner` runs a candidate's checks while it holds the slot.
  `parallelChecks` (default 1) bounds how many run at once, and `parallelSafe` marks which may share.
- **Cost is recorded per delivery, for checks only.** `record_history` keeps each check's last five passing
  durations in `meta.checkHistory`, along with its flake count and the last five whole runs. Nothing records CPU,
  and nothing covers `fullHealth`, `lead-suite` or reviewer runs. `scripts/run-tests.py --timings` records
  per-unit times for this repository's suite only.
- **Heavy runs outside the queue.** `fullHealth` (`.venv/bin/python -B scripts/run-tests.py --workers 4`, about 70
  minutes) runs after every push to the base, as a supervisor background child (`start_full_health` in
  `run_delivery.py`). `lead-suite` runs the same argv in a detached checkout. `run_delivery.py check` runs checks
  through `merge_queue.py run-checks`, and so do `gate` guards and `lead-push` guards. Reviewers' module and mutation
  runs are plain commands that no tool wraps.
- **The hand-kept coordination lives outside this repository.** The ticket describes a suite slot script
  (`agent-skills-worktrees/.suite-slot/suite-slot.sh`). It allows one full suite at a time, waits while another
  project's quiet-machine lease is held, and only lanes use it. It was not at that path on this machine on
  2026-11-20, so this plan works from the ticket's description and from Athena's `docs/harness.md`. In Athena,
  `full-health` honours `ATHENA_QUIET_MACHINE_LEASE`, a file whose existence makes the run wait, and
  `ATHENA_SUITE_SLOT_LOCK`, a `lockf` file that each heavy check holds, with `ATHENA_FULL_HEALTH_LEASE_WAIT_SECONDS`
  capping both waits.
- **`leases` already means something else.** The binding's `leases` (`schemas.json`, `run_lead_input`) are
  lead-input leases, such as a shared dev deployment. This plan's primitive is therefore called a **claim**, never a
  lease.

The paired TypeScript harness has no pressure, slot or wait code. A search of `product-runtime/` and
`agent-delivery-harness/` for `loadavg`, `merge-slot` and `mergeSlot` finds none. This plan changes Python and
documentation only.

## What a reader should be able to do

- Any agent or view can read who is using the machine right now: each run's slots, its label, its repository,
  delivery and story, when it started and when it is expected to end, plus which runs are waiting and in what order.
- Every heavy command the delivery tools start claims slots by default, and says what it claimed, what it expects to
  take, and who else holds what.
- When the machine is full, a command waits by default, names what it is waiting behind and when that should free
  up, and says how many slots are free now. An agent can run anyway with a reason, or admit a waiting run with a
  reason. Both are recorded.
- A lead sees claims, waits and overrides in its wait and in the delivery views. The machine's history shows over
  time whether the defaults are right.

## Finish line

1. In this repository, with capacity set to 4, a queue check and a `run_delivery.py claim` run asking for 4 slots
   don't run at once. The second waits. Its output names the first claim's label, delivery, slots and expected end.
   It starts once the first claim is released. (Scenarios 1 and 3)
2. The same second run started with `--now --why <text>` runs at once. The override is recorded in the machine
   history and, for a run inside a delivery, journaled as `decision.recorded`. (Scenarios 4 and 12)
3. A claim whose process dies without releasing is gone from the ledger the next time anything reads it, and the
   runs behind it are admitted. (Scenario 6)
4. After one recorded run of a kind, the next claim of that kind carries a measured estimate of its wall seconds and
   slots. (Scenarios 8 and 9)
5. No command waits on capacity while it holds a merge slot. (Scenario 10)
6. Settings that set nothing new behave as before, except that heavy commands now claim and may wait. Every existing
   test passes unchanged, apart from the pins listed under Stories.
7. The full suite passes on macOS with Python 3.11 within `tests/suite-budget.json`, and no test reads or writes the
   real machine directory. (Scenario 14)

## Decisions

1. **Primitives, defaults and recorded overrides, with no prescribed rules.** The tool gives agents three facts
   (who is using the machine, what a run costs, what the machine can bear) and the commands to act on them.
   - Commands do the common thing by default.
   - An agent steps off a default with `--now --why <text>` or `admit --why <text>`, and its reason is recorded.
     `--now` without `--why` is a usage error, exit 2.
   - The tool refuses only to protect shared state: the ledger's own files (Decision 3) and the merge slot (Decision
     8). It never refuses a run for capacity alone.
   - The tool sets no rule about workflow choices. It doesn't say whether to overlap a hand-off suite with an open
     review round, which of a story's heavy runs to keep, or when to push. The briefs (Decision 11) name the
     primitives and the defaults, not a policy.
   - Every story's criteria include that no fixed rule replaces the agent's judgement.
2. **One machine directory, shared by every repository on the machine (deviates from the ticket; operator
   confirms).** The ticket puts the ledger beside the git common directory, as the merge slots are. That location is
   one per repository, but capacity is one per machine. A ledger per repository can't see Athena's runs, so it can't
   replace Athena's quiet-machine lease or the suite slot that spans both repositories. The merge slot stays where it
   is, because it guards a base, which belongs to one repository.
   - The directory is `$AGENT_SKILLS_MACHINE_DIR` when that is set. Otherwise it is
     `$XDG_STATE_HOME/agent-skills/machine`, falling back to `~/.local/state/agent-skills/machine` on POSIX, and
     `%LOCALAPPDATA%\agent-skills\machine` on Windows, which is not validated.
   - It holds `ledger.lock`, `claims/`, `capacity.json`, `costs.json` and `history.jsonl`. Each file is two-space
     JSON with a final newline, except `history.jsonl`, which has one JSON object per line and keeps its last
     1,000 entries.
   - Each repository identifies itself by its resolved git common directory, the same identity its merge slots use.
3. **A claim is one file, locked by its process for as long as it lives.** A request writes
   `claims/<uuid>.json` and holds an OS-released lock on it (`file_lock`, one open file per claim) from enqueue to
   release. Fields:
   - `id`, `state` (`waiting` or `running`), `slots`, `exclusive`;
   - `kind`, one of `check`, `fullHealth`, `suite` or `run`, and `subject` (the check id, `lead-suite`, or the
     caller's label);
   - `label`, `repository`, `delivery` and `story` when known, and `pid`;
   - `enqueuedAt`, `startedAt`, `expectedEnd`, and `estimate` (`{seconds, slots, from}`, where `from` is `measured`
     or `none`);
   - `admittedBy`, which is `capacity`, `alone`, `slot`, `now` or `admit`, with `why` for the last two.

   **Liveness comes from the lock alone.** A reader that can take a claim file's lock knows its holder is gone, and
   removes the file. The pid is for display only. This mirrors `merge_slot`.

   **Every change to the ledger happens under `ledger.lock`.** That covers enqueue, admission, state changes and
   release. Release removes the claim file and drops its lock, even when the run fails, times out or is
   interrupted.
4. **Admission rule.** `used` is the sum of `slots` over live `running` claims. A waiting claim is admitted when one
   of these holds:
   - **Capacity.** No earlier live waiter is still waiting, no exclusive claim is running, and `used + slots ≤
     capacity`.
   - **Alone.** Nothing is running, which lets a claim larger than capacity run alone instead of never.
   - **Exclusive.** For an exclusive claim, nothing is running and no earlier live waiter is still waiting. While
     an exclusive claim runs, nothing else is admitted except by `now`, `admit` or `slot`.
   - **Slot.** The claim is made while its process holds a merge slot. Decision 8 covers this.
   - **Now.** It was started with `--now --why`.
   - **Admit.** Another agent ran `admit <id> --why`.

   Waiters are served strictly first come, first served, with no backfill, so a large `fullHealth` claim is never
   starved by a stream of small ones. An agent that wants a small run to go first overrides, and the override is
   recorded. A waiter checks again every 2 s and whenever a release happens.
5. **Waiting is the default, and it is uncapped unless the caller caps it.** A waiting command prints one line when
   it starts waiting and one each time the ledger changes. Each line says:
   - the slots it asked for and the slots in use against the capacity;
   - each running claim, with its label, delivery, slots and expected end;
   - how many waiters are ahead of it;
   - how many slots are free now, as in "2 slots are free now; a run of at most 2 would start at once".

   `--wait-seconds N` caps the wait. A capped wait that ends unadmitted exits 75 (`EX_TEMPFAIL`), names the holders,
   runs nothing and records a `gave-up` history entry. The queue and `fullHealth` wait uncapped, and their waits show
   on the record and wake the lead (Decision 10). A hung holder is still bounded by its own timeout.
6. **Cost is measured, not declared.** `costs.json` keeps the last five completed runs for each `(repository, kind,
   subject)`. Each run is stored as `{seconds, cpuSeconds, result, at}`.
   - The runner reaps its child with `os.wait4` (polled, so timeouts still apply), so `cpuSeconds` covers that child
     and every descendant it waited for. Where `wait4` is missing, as on Windows, `cpuSeconds` is `null`.
   - The estimate is the median `seconds` over the last five runs. Its `slots` is the median of `ceil(cpuSeconds /
     seconds)` over the runs that have both, clamped to between 1 and capacity.
   - With no record, or no CPU figures, a claim takes the caller's `--slots` (default 1), and its `estimate.from` is
     `none`. A first run can therefore under-claim. The next one is measured. No binding field declares a cost,
     which is consistent with V26-3645's Decision 8.
   - Failed and timed-out runs are recorded with their result, but only `passed` runs (exit 0) feed the estimate.
   - `meta.checkHistory` stays as it is. It is the delivery's own view of check durations and flakes, and nothing in
     this plan reads or replaces it.
7. **Capacity is one number per machine.** `capacity.json` holds `{slots, setAt, why}`.
   - Unset, capacity is `os.cpu_count()`.
   - `machine.py capacity --slots N --why <text>` sets it, `--reset --why <text>` removes it, and both record a
     history entry.
   - `machine.py status` shows capacity beside the live pressure from `machine_reading` (load, free disk and swap), so
     claims and the machine's real state are read side by side. Pressure is information: it changes no admission.
8. **A claim comes before the merge slot, and never waits while one is held.** Only one lock order is allowed:
   claim, then slot.
   - Any claim made or grown while its process holds a merge slot is admitted at once (`admittedBy: slot`). It still
     counts in `used`, so other runs wait for it.
   - The queue therefore claims for a turn **before** taking the base's slot. The claim's `slots` is the largest
     estimated `slots` among the checks it expects to run, multiplied by `min(parallelChecks, number of those
     checks)`. Those checks are the candidate's lead selection when one is recorded, otherwise the binding's
     `alwaysRun` and `fallback` checks. Once it holds the slot, the queue grows or shrinks the claim to the checks it
     actually selected, and a grow is admitted at once.
   - `lead-push` claims for its guards the same way before it takes the slot.
   - Waiting for capacity while holding the slot would stall every other delivery's push and sync on that base. That
     is shared state, so this is the one place the tool's ordering is fixed.
   - The merge slot itself is **kept** unchanged (V26-3318).
9. **Which commands claim by default.**

   | Command | `kind` / `subject` | Claims | Notes |
   | --- | --- | --- | --- |
   | queue turn (`merge_queue.py` serve and check) | `check` / turn, then each check id | before the slot (Decision 8) | Cost is recorded per check id. |
   | `merge_queue.py run-checks` (behind `run_delivery.py check`, `gate` guards and `lead-push` guards) | `check` / check id | around each check | `--now --why` and `--wait-seconds` pass through. |
   | post-merge `fullHealth` (supervisor child) | `fullHealth` / `fullHealth` | around the run | Waits uncapped. Its wait shows as the base's verification waiting. |
   | `lead-suite` | `suite` / `lead-suite` | around the run | Same argv as `fullHealth`, but recorded separately, because a lead may pass extra arguments. |
   | `run_delivery.py claim -- <argv>` | `run` / `--label` | around argv | This is the paved road for a lane's hand-off suite and reviewers' module and mutation runs (Decision 11). |

   `gate`'s inline repository gates, hooks, `prepare` steps and git calls don't claim. They are short, and `prepare`
   runs inside a claim that is already held.
10. **The lead sees the machine where it already looks.**
    - `lead-wait` adds a `machine` object to every wake: capacity, used, the running claims, the waiters and the last
      five overrides.
    - It also wakes when one of its own delivery's claims has waited longer than `--claim-wait-seconds` (default
      900). This happens once per waiting stretch, tokened like a blocked lane, so a restart doesn't wake on it
      again. The lead decides whether to admit it, and nothing is admitted automatically.
    - The built-in page and the markdown renderer gain a **Machine** section beside peers. It shows capacity and live
      pressure, then running claims (label, repository, delivery, slots, since, expected end), then waiters in order,
      then the last five overrides with their reasons. It is read live at render time, the way the merge slot's
      holder is read today, and is left out when the machine directory is empty.
    - No new journal kinds are added. Overrides inside a delivery use `decision.recorded`. Waits and admissions live in
      the machine history, which covers every repository.
11. **Briefs name the primitives, not a policy.** The lane brief and each lens round brief from `review-prepare`
    say three things: that `run_delivery.py claim -- <argv>` is the default way to start a heavy command, that
    `machine.py status` shows the machine, and that `--now --why` is how to step off. They say nothing about when to
    run what.
12. **Rulings on what exists.**
    - **Suite slot script: replaced.** The ledger covers what it did: one full suite at a time becomes slot
      accounting, and waiting on a quiet-machine lease becomes an exclusive claim. The script is outside this
      repository, so retiring it is an operator step, taken once Athena claims (follow-up F1).
    - **Quiet-machine lease (`ATHENA_QUIET_MACHINE_LEASE`): replaced** by `machine.py run --exclusive --label <why>
      -- <argv>`. That covers a benchmark that needs a quiet machine. Athena moves over in F1. Until then, Athena's
      variables keep working, because this plan changes nothing in Athena.
    - **Shared merge slot (V26-3318): kept.** It guards a base, not capacity. Decision 8 sets its order against
      claims.
    - **Reader checks before any round (V26-3592): built on.** That ticket is in Backlog and unchanged by this plan.
      The checks it proposes run through `run_delivery.py check`, and the lens checkouts it prepares run their checks
      the same way, so they claim with no change to its scope.
13. **One module, standalone at its edges.** The new `skills/orchestrate-work/scripts/machine.py` holds the ledger,
    the admission, the cost record, the capacity and the CLI (`status`, `run`, `admit`, `capacity`).
    - It imports `file_lock`, `machine_reading`, `now` and the atomic writer from `delivery_state.py`.
      `delivery_state.py` imports `machine` only inside `wait_for_lead`, so no import cycle forms.
    - `merge_queue.py` and `run_delivery.py` import it from the same installed release they run from.
14. **Tests never touch the real machine directory.** `tests/__init__.py` sets `AGENT_SKILLS_MACHINE_DIR`
    unconditionally, for every test process, to a fresh directory under `tempfile.gettempdir()`, as it already sets
    `GIT_TEMPLATE_DIR`. A pin asserts this.
    - A fixture queue a test starts inherits that variable, so it uses the test's own ledger.
    - A real `fullHealth` run of the suite claims in the real ledger from the supervisor's process. The suite inside
      it uses its own ledgers, so the suite never queues behind its own claim.
15. **Release.** This is a runtime change, so it goes out first as a candidate (`-rc.N`), is applied in this
    repository's own deliveries, and is then promoted. It is additive (a minor version). No delivery record shape,
    binding field or journal kind changes. The machine files' shapes (`machine-claim/1`, `machine-capacity/1`,
    `machine-costs/1`, `machine-history/1`) are added to `schemas.json`.
16. **Plan review has no round bound.** It runs until its lenses align, or until the operator ends it.

## Out of scope

- Changing Athena. F1 is filed under V26-3638, in Athena's project, to make Athena's `full-health` checks and
  benchmark claim through the installed `machine.py`, and to drop `ATHENA_SUITE_SLOT_LOCK` and
  `ATHENA_QUIET_MACHINE_LEASE`. Retiring `suite-slot.sh` is an operator step after F1.
- The line map (`delivery-views`). F2 is filed under V26-3638 to draw the Machine section on it from the same reading.
  It is left out here because the hosted viewer patches `line-map.js` at fixed hooks, a cost V26-3592 records.
- Memory as a cost dimension. Swap and free disk stay as information beside the ledger (Decision 7).
- Admission from live load. Load reflects runs that don't claim, and acting on it would bring back the fixed rule
  the ticket rules out. It is shown, not acted on.
- Choosing a run's worker count. The tool says how many slots are free, and the agent sizes its run.
- Wrapping `scripts/run-tests.py` itself. It is this repository's script, not a released one. Its callers claim.
- Any change to `meta.checkHistory`, to the merge slot's files, or to the TypeScript harness.

## Approval Handoffs

- **D2 needs the operator's confirmation:** the ledger is per machine, not beside the git common directory.
- The operator approves this plan once its review aligns. The stories are then filed under V26-3638, and their keys
  replace `MC-1` to `MC-4`. F1 and F2 are filed as follow-ups.
- The operator chooses the delivery base, `main` or an integration branch, at `lead-setup`.
- V26-3573 (In Progress) changes the lead-wait fixture tests. MC-4 starts on a base that has V26-3573 merged.

## Execution posture and sensors

The posture is test-first. Each story's sensors:

- its touched test modules, through `.venv/bin/python -m unittest <modules>`;
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`;
- `tests.test_linear_qualification` and `tests.test_registry`, the binding's guards, which run at `gate`;
- the full suite with `--workers 4 --budget tests/suite-budget.json`, run by the queue and by `fullHealth`, not by
  lanes.

**Test cost.** Ledger and admission tests run in-process, with an injected clock and sleep. Only Scenario 6 starts a
process, one child that takes a claim and is killed, and that test reuses one fixture across its cases. Queue and
`run_delivery` scenarios reuse the fixture classes `tests/test_merge_queue.py` and `tests/test_run_delivery.py`
already build. No class needs `run_serially`. Scenario 6 is `run_quietly`, because it waits on a real process
exiting.

**Shared resources.** Each test's machine directory is its own (Decision 14). No ports. The merge slot is used only by
existing fixtures, under their own git common directories.

## Scenarios

| # | Story | Given | Then |
| --- | --- | --- | --- |
| 1 | MC-1 | capacity 4; claim A of 4 running | claim B of 1 waits. Its line names A's label, delivery, slots and expected end, and says 0 slots are free. |
| 2 | MC-1 | capacity 4; A of 2 running; B of 3 waiting; C of 1 arrives | C waits behind B (no backfill), although 2 are free. |
| 3 | MC-1 | as 1, then A is released | B is admitted with `admittedBy: capacity`. The history records the wait's length. |
| 4 | MC-1 | as 1, with B run `--now --why x` | B runs at once with `admittedBy: now, why: x`. The history records an override entry. `--now` alone exits 2. |
| 5 | MC-1 | as 1; another process runs `admit <B> --why y` | B is admitted with `admittedBy: admit`, and the history names y. |
| 6 | MC-1 | a child process takes a claim of 4 and is killed with SIGKILL | the next read removes its file. A waiting claim is admitted. No pid is signalled. |
| 7 | MC-1 | capacity 4; nothing running; claim of 6 | admitted `alone`. Later claims wait until it is released. |
| 8 | MC-1 | no cost record; claim `--slots 3` | slots 3, `estimate.from: none`. |
| 9 | MC-1 | five passed runs of 600 s wall and 2,400 s CPU, plus one failed run | estimate `{seconds: 600, slots: 4, from: measured}`. The failed run is stored but ignored. |
| 10 | MC-2 | capacity 1; a `run` claim of 1 running; a queue turn starts | the queue waits **before** taking the merge slot. The base's slot has no holder file and is not locked while the queue waits. |
| 11 | MC-2 | the queue holds the slot and selects more checks than it pre-claimed | the claim grows with `admittedBy: slot` while capacity is full, and other waiters see the larger `used`. |
| 12 | MC-3 | a lane runs `run_delivery.py claim --now --why z -- <argv>` while full | argv runs. The delivery journals `decision.recorded` with z, and the history matches it. |
| 13 | MC-3 | `fullHealth` starts while capacity is full | it waits uncapped. Its claim is visible as `kind: fullHealth`, and once admitted it runs as before. |
| 14 | MC-1 | any test process | `AGENT_SKILLS_MACHINE_DIR` is set, under `tempfile.gettempdir()`, and differs from the real default. |
| 15 | MC-4 | a delivery's claim has waited 901 s with `--claim-wait-seconds 900` | `lead-wait` wakes once, with the claim in `machine`. A restart that carries the token stays quiet while it waits. |
| 16 | MC-4 | a machine directory with two running claims, one waiter and one override | the page and the markdown show the Machine section with all four. With an empty directory, neither shows the section. |
| 17 | MC-1 | capacity 4; exclusive claim E waiting behind A of 1; then claim C of 1 arrives | E is admitted once A is released. C waits until E is released. |
| 18 | MC-1 | `--wait-seconds 5` while full | exits 75 after 5 s, runs nothing, names the holders, and records `gave-up`. |

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | MC-1 | `skills/orchestrate-work/scripts/machine.py`, `skills/orchestrate-work/references/schemas.json`, `tests/test_machine.py`, `tests/__init__.py`, `profiles/`, `tests/test_orchestration_adopter.py` |
| 2 | 1 | MC-2 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py` |
| 2 | 2 | MC-3 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py` |
| 2 | 3 | MC-4 | `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`, `docs/own-deliveries.md`, `tests/test_lead_wait.py`, `tests/test_orchestration_render.py` |

`provenance.lock.json` is shared by every lane. Each lane runs the provenance writer. A conflict on it is resolved by
the AGENTS.md rule: take either side, rerun the writer, then validate the registry. Only MC-1 adds a listed output
(`machine.py`).

## Stories

### MC-1. Keep a machine ledger of claims, measured costs and a capacity

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/machine.py`,
`skills/orchestrate-work/references/schemas.json`, `tests/test_machine.py`, `tests/__init__.py`, `profiles/` and
`tests/test_orchestration_adopter.py`.

Decisions 2 to 7, 13 and 14, with the `machine.py` CLI (`status`, `run`, `admit`, `capacity`, with `--json` on
`status`). Scenarios 1 to 9, 14, 17 and 18.

Pins to re-derive:
- `machine.py` is added to the five profiles that list `region_ownership.py` (`core`, `delivery-views`, `jira`,
  `linear` and `run-delivery`);
- it is added to the script list at `tests/test_orchestration_adopter.py:982`;
- it is added to `provenance.lock.json` as a new output.

Criteria:
- the scenarios pass;
- `status` prints capacity and pressure;
- no admission reads load;
- no fixed rule replaces the agent's judgement.

### MC-2. Claim for the queue's checks and the lead's guards before the merge slot

Lane 1, tier 2, after MC-1. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py` and
`tests/test_merge_queue.py`.

Decisions 8 and 9, for the queue turn, `run-checks` (`--now --why`, `--wait-seconds`) and `lead-push`'s guards.
Each check's cost is recorded per check id. The queue's current step shows "waiting for capacity" with the holders,
through `meta.checking.now`'s existing steps. Scenarios 10 and 11, plus one pin that no code path waits on capacity
while holding a slot: a claim taken under a held slot is always admitted `slot`.

Criteria:
- the scenarios pass;
- existing queue tests pass unchanged, with an empty ledger and capacity left at the default;
- no fixed rule replaces the agent's judgement.

### MC-3. Claim for fullHealth, lead-suite and any heavy run, and name the claim in the briefs

Lane 2, tier 2, after MC-1. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery.py` and
`tests/test_run_delivery_supervisor.py`.

Decisions 9 and 11. It covers:
- `run_delivery.py claim [--slots N] [--label L] [--exclusive] [--now --why W] [--wait-seconds S] -- <argv>`, which
  fills in the delivery and story from the lane's context and journals `decision.recorded` on `--now`;
- claims for `fullHealth` and `lead-suite`;
- brief text in the lane brief and the lens round brief.

Scenarios 12 and 13.

Pins to re-derive: any brief-text assertion in `tests/test_run_delivery.py` that compares a whole brief.

Criteria:
- the scenarios pass;
- a corpus pin holds the brief sentence naming `claim` and `--now --why`;
- an absence pin shows the brief carries no rule about when to run what. It searches the lane brief and the lens
  round brief, by meaning, for "only one", "never overlap", "must wait" and "do not run".
- no fixed rule replaces the agent's judgement.

### MC-4. Show the machine in the lead's wait and the delivery views

Lane 3, tier 2, after MC-1. Test-first. Starts on a base with V26-3573 merged. Files:
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/render_state.py`,
`skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`,
`docs/own-deliveries.md`, `tests/test_lead_wait.py` and `tests/test_orchestration_render.py`.

Decision 10. It covers:
- the `lead-wait` `machine` object and `--claim-wait-seconds`;
- the Machine section in the built-in page and the markdown;
- the guide's account of the primitives, the defaults and the overrides;
- `docs/own-deliveries.md`, which covers setting capacity and running an exclusive claim, and says that the suite slot
  is retired once F1 lands.

Scenarios 15 and 16.

Pins to re-derive: whole-payload assertions on `wait_for_lead` results in `tests/test_lead_wait.py`, and page
snapshots in `tests/test_orchestration_render.py` for a non-empty machine directory only. An empty directory renders
as before.

Criteria:
- the scenarios pass;
- existing render snapshots are unchanged when the machine directory is empty;
- no fixed rule replaces the agent's judgement.
