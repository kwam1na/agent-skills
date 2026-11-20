---
reviewSlug: shared-machine-capacity
date: 2026-11-20
execution_posture: test-first
context: A delivery's heavy runs share one machine, but no agent can see what the others are running, what a run will cost, or what the machine can bear. This plan adds three primitives (a claims ledger, a measured cost record and a capacity setting) and puts defaults on top of them. Heavy commands claim by default and wait when the machine is full. Any agent can step off a default with a reason, and the reason is recorded. Nothing refuses except to protect shared state.
---

# Shared-machine capacity: claims, costs and capacity

The design ticket is [V26-3638](https://linear.app/v26-technologies/issue/V26-3638), and this plan is its outcome.
The epic that delivers it is [V26-3689](https://linear.app/v26-technologies/issue/V26-3689), and its four stories are
V26-3680 to V26-3683. The follow-ups are V26-3684 (Athena claims through the ledger) and V26-3685 (the line map).
Each is related to the epic rather than filed as its child, so a delivery of the epic's children doesn't take them in
as its own stories.

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
  `merge_queue.py:3853`) and the lead's push, sync, upgrade, check, merge and release commands hold it. The queue's
  wait for it is bounded by `checkTimeoutSeconds`, after which serve retries.
- **The queue runs checks inside the slot.** `Runner` runs a candidate's checks while it holds the slot.
  `parallelChecks` (default 1) bounds how many run at once, and `parallelSafe` marks which may share.
- **Cost is recorded per delivery, for checks only.** `record_history` keeps each check's last five passing
  durations in `meta.checkHistory`, along with its flake count and the last five whole runs. Nothing records CPU,
  and nothing covers `fullHealth`, `lead-suite` or reviewer runs. `scripts/run-tests.py --timings` records
  per-unit times for this repository's suite only.
- **Heavy runs outside the queue.**
  - `fullHealth` (`.venv/bin/python -B scripts/run-tests.py --workers 4`, about 70 minutes) runs after every push to
    the base. The supervisor starts it as `full-health-run` (`full_health_run`, around `run_delivery.py:12610`), a
    process of its own that outlives supervisor restarts.
  - `lead-suite` runs the same argv in a detached checkout.
  - `run_delivery.py check`, `gate` guards and `lead-push` guards all reach `merge_queue.py run-checks` through
    `check_runs` (around `run_delivery.py:3074`). `check_runs` pipes stdout and stderr and reads them only when the
    process exits. Its stdout must be one JSON document.
  - `lead_push` runs its guards (`push_guards`) before it takes the merge slot.
  - `gate` and `lead-push` already take `--why`, and refuse it without `--skip-guards`.
  - Reviewers' module and mutation runs are plain commands that no tool wraps.
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

1. In this repository, with capacity set to 4, a queue turn and a `run_delivery.py claim` run asking for 4 slots don't
   run at once, in either order. The second waits. Its output names the first claim's label, delivery, slots and
   expected end. It starts once the first claim is released. (Scenarios 10 and 19)
2. The same second run started with `--now --why <text>` runs at once. The override is recorded in the machine
   history and, for a run inside a delivery, journaled as `decision.recorded`. (Scenarios 4 and 12)
3. A claim whose process dies without releasing is gone from the ledger the next time anything reads it, and the
   runs behind it are admitted. A claim whose process is alive is never removed, whatever its pid field says.
   (Scenario 6)
4. After one recorded passing run of a kind, made through the real runner, the next claim of that kind carries a
   measured estimate of its wall seconds and slots. (Scenarios 9 and 20)
5. No command waits on capacity while it holds a merge slot, and a claimed command never waits behind its own
   ancestor's claim. (Scenarios 10, 11a to 11d, 21, 22 and 31)
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
2. **One machine directory, shared by every repository on the machine (the operator confirmed this on
   2026-11-20).** The ticket puts the ledger beside the git common directory, as the merge slots are. That location
   is one per repository, but capacity is one per machine. A ledger per repository can't see Athena's runs, so it
   can't replace Athena's quiet-machine lease or the suite slot that spans both repositories. The merge slot stays
   where it is, because it guards a base, which belongs to one repository.
   - **Where it is.** The first of these that applies:
     1. `$AGENT_SKILLS_MACHINE_DIR`, when it is set and non-empty;
     2. `$XDG_STATE_HOME/agent-skills/machine`, when `XDG_STATE_HOME` is set and non-empty;
     3. `~/.local/state/agent-skills/machine` on POSIX;
     4. `%LOCALAPPDATA%\agent-skills\machine` on Windows, which is not validated.
   - **What it holds.** `ledger.lock`, `claims/`, `capacity.json`, `costs.json` and `history.jsonl`. Each file is
     two-space JSON with a final newline, except `history.jsonl`, which has one JSON object per line and keeps its
     last 1,000 entries.
   - **How a repository is named.** Each repository identifies itself by its resolved git common directory, the
     same identity its merge slots use.
3. **A claim is one file, locked by its process for as long as it lives.** A request writes `claims/<uuid>.json` and
   holds an OS-released lock on it (`file_lock`, one open file per claim) from enqueue to release. Fields:
   - `id`, `state` (`waiting` or `running`), `slots`, `exclusive`;
   - `kind`, one of `check`, `fullHealth`, `suite` or `run`, and `subject` (the check id, `turn`, `lead-suite`, or
     the caller's label);
   - `label`, `repository`, `delivery` and `story` when known, and `pid`;
   - `enqueuedAt`, `startedAt`, `expectedEnd`, and `estimate` (`{seconds, slots, from}`, where `from` is `measured`
     or `none`);
   - `admittedBy`, which is `capacity`, `alone`, `slot`, `now` or `admit`, with `why` for the last two.

   **Liveness comes from the lock alone, in both directions.** A reader that can take a claim file's lock knows its
   holder is gone, and removes the file, whatever process its `pid` field names. A reader that can't take it counts
   the claim as live, even when its `pid` names no process. The pid is for display only, and no reader signals or
   probes it. This mirrors `merge_slot`.

   **Every change to the ledger happens under `ledger.lock`.** That covers enqueue, admission, state changes and
   release.

   **Release is unconditional.** A claim is released in a `finally`: on exit 0, on any other exit, on the runner's
   timeout, on `KeyboardInterrupt` or another exception, and on a signal the runner handles. Release removes the
   claim file and drops its lock. A long-lived process, such as the queue's serve loop, therefore never keeps a
   claim past the run it was for.

   **A ledger that can't be used doesn't stop the run.** The ledger is unavailable only when the directory, or
   `ledger.lock` within it, can't be created or opened. In that case the command says `machine ledger unavailable:
   <reason>; running unclaimed` on stderr and runs as it would have before this plan. The views and `lead-wait` show
   the ledger as unavailable. The ledger is information for agents' calls, so losing it must not halt work.

   Ordinary contention never counts as unavailable:
   - a `lock_timeout` on `ledger.lock` is retried, as part of the wait;
   - a claim file that a concurrent release removed between the listing of `claims/` and its read is skipped;
   - a malformed claim file whose lock is held counts as live with the slots it last said, or with 1 when it says
     none; one whose lock is free is removed, by the liveness rule above.
4. **Admission rule.** `used` is the sum of `slots` over live `running` claims. A waiting claim is admitted when one
   of these holds:
   - **Capacity.** No earlier live waiter is still waiting, no exclusive claim is running, and `used + slots ≤
     capacity`.
   - **Alone.** No earlier live waiter is still waiting and nothing is running. This lets a claim larger than
     capacity run alone instead of never.
   - **Exclusive.** For an exclusive claim, no earlier live waiter is still waiting and nothing is running. While
     an exclusive claim runs, nothing else is admitted except by `now`, `admit` or `slot`.
   - **Slot.** The claim is made while its own process holds a merge slot (Decision 8).
   - **Now.** It was started with `--now --why`.
   - **Admit.** Another agent ran `admit <id> --why`.

   Waiters are served strictly first come, first served, with no backfill, so a large `fullHealth` claim is never
   starved by a stream of small ones. An agent that wants a small run to go first overrides, and the override is
   recorded. A waiter checks again every 2 s and whenever a release happens.

   **Nested claims.** `machine.py run`, `run_delivery.py claim` and every claiming command export
   `AGENT_SKILLS_MACHINE_CLAIM=<id>` to the process they start.
   - A claim requested while that variable names a live claim (by Decision 3's lock test) is not taken. The command
     runs at once under its ancestor's claim and says `running under claim <id> (<label>)`.
   - Its run's cost is still recorded under its own `(kind, subject)`.
   - A variable naming a claim that is gone is ignored, and the command claims as usual.
   - `check_runs` and the queue pass the variable to the processes they start.
   - **Into checks too.** The queue and `run-checks` also set these in every check's and `prepare` step's declared
     environment:
     - `AGENT_SKILLS_MACHINE_CLAIM`, naming the turn's claim in the queue and the check's claim in `run-checks`;
     - `AGENT_SKILLS_MACHINE_DIR` and `XDG_STATE_HOME`, when set.

     They are runner-owned names, added to `RUNNER_OWNED` (`merge_queue.py:573`). They are also left out of a run
     record's environment `names`, so neither the digest nor `fact_differences`' comparison of names includes them.
     A record made with or without them, including one written before this change, compares as it did. A check whose own argv claims therefore runs under its turn's claim. It never waits
     behind it, and it uses its runner's ledger.
5. **Waiting is the default, and it is uncapped unless the caller caps it.** A waiting command prints, on stderr only,
   one line when it starts waiting and one each time the ledger changes. Each line says:
   - the slots it asked for and the slots in use against the capacity;
   - each running claim, with its label, delivery, slots and expected end;
   - how many waiters are ahead of it;
   - how many slots are free now, as in "2 slots are free now; a run of at most 2 would start at once".

   `--wait-seconds N` caps the wait. A capped wait that ends unadmitted exits 75 (`EX_TEMPFAIL`), names the holders,
   runs nothing and records a `gave-up` history entry. The queue and `fullHealth` pass no cap. Their waits show in
   the ledger and wake the lead (Decision 10). A hung holder is still bounded by its own timeout.
6. **Cost is measured, not declared.** `costs.json` keeps two lists for each `(repository, kind, subject)`: the last
   five **passed** runs, and the last five runs that failed or timed out. Each run is stored as `{seconds,
   cpuSeconds, result, at}`.
   - The runner reaps its child with `os.wait4`, polled so timeouts still apply, so `cpuSeconds` covers that child and
     every descendant it waited for. Where `wait4` is missing, as on Windows, `cpuSeconds` is `null`.
   - The estimate's `seconds` is the median of `seconds` over the passed list.
   - The estimate's `slots` is the median of `ceil(cpuSeconds / seconds)` over the passed runs that have both,
     clamped to between 1 and capacity.
   - With no passed run, a claim takes the caller's `--slots` (default 1) and `estimate.from: none`. With passed runs
     but no CPU figures, `seconds` is measured while `slots` is the caller's `--slots`, and `from` is `measured`.
   - A first run can therefore under-claim. The next one is measured. No binding field declares a cost, which is
     consistent with V26-3645's Decision 8.
   - Failed and timed-out runs never feed the estimate, and a flaky check can't push its passed runs out of the list.
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
   - **Holding a slot.** "Its own process holds a merge slot" means that process has entered `machine.py`'s
     process-local `holding_slot()` context, which the queue enters for exactly as long as it holds the base's
     `merge_slot`. `machine.py` reads only that marker, never the slot's files or the holder's pid. A claim from any
     other process, a child of the holder included, waits as usual. The queue is the only command that claims while
     holding a slot: `lead-push` and the lead's other slot commands claim nothing under it.
   - **A claim made or grown under the slot is admitted at once** (`admittedBy: slot`). It still counts in `used`,
     so other runs wait for it.
   - **The queue claims once per turn, before the slot** (`kind: check`, `subject: turn`). The claim's `slots` is the
     largest estimated `slots` among the checks it expects to run, multiplied by `min(parallelChecks, number of those
     checks)`. Those checks are the candidate's lead selection when one is recorded, otherwise the binding's
     `alwaysRun` and `fallback` checks.
   - **A turn keeps its claim across a slot retry.** When the turn's wait for the slot ends in `queue_locked`, it keeps
     its claim through serve's retry, so it doesn't lose its place to later arrivals. It releases the claim when the
     turn ends, in a `finally`, as Decision 3 says. `holding_slot()` clears its marker in a `finally` too. serve
     releases a kept claim, in a `finally`, as soon as a pass takes no turn and whenever serve itself ends. A pass
     takes no turn when the queue is held, nothing is queued, the base awaits repair, or a setup step stops.
   - **Under the slot, the turn claim is resized** to the same formula over the checks the queue actually selected.
     It grows at once under the slot, or shrinks. The queue takes no claim per check. Each check's cost is still
     recorded under `(check, <id>)`.
   - **`lead-push` takes no claim of its own.** Its guards already run through `run-checks` before it takes the
     slot, so they claim there. A slow guard therefore waits before the slot, never while holding it.
   - **Why the order is fixed.** Waiting for capacity while holding the slot would stall every other delivery's push
     and sync on that base. That is shared state, so this is the one place the tool fixes the order.
   - The merge slot itself is **kept** unchanged (V26-3318).
9. **Which commands claim by default.**

   | Command | `kind` / `subject` | Claims | Notes |
   | --- | --- | --- | --- |
   | queue turn (`merge_queue.py` serve and check) | `check` / `turn` | once, before the slot, resized under it (Decision 8) | Cost is recorded per check id. |
   | `merge_queue.py run-checks`, behind `run_delivery.py check`, `gate` guards and `lead-push` guards | `check` / check id | around each check | Takes `--now --why` and `--wait-seconds`. Its waiting lines go to stderr. |
   | `fullHealth`, in the `full-health-run` process | `fullHealth` / `fullHealth` | around the run | No wait cap. It is held by `full-health-run`, not by the supervisor, so a supervisor restart neither releases it nor blocks on it. `full-health-run` takes its claim only after it holds the run's lock and has re-checked that no result stands at the head. It records the claim id in the lock file, so `full_health_running` reports the waiting run and a second start runs nothing. |
   | `lead-suite` | `suite` / `lead-suite` | around the run | Same argv as `fullHealth`, but recorded separately, because a lead may pass extra arguments. |
   | `run_delivery.py claim -- <argv>` | `run` / `--label` | around argv | This is the paved road for a lane's hand-off suite and reviewers' module and mutation runs (Decision 11). |

   `run_delivery.py check`, `gate`, `lead-push` and `lead-suite` take `--now`. It forwards to `run-checks` with the
   command's `--why`. The existing refusal "`--why` goes with `--skip-guards`" becomes "`--why` goes with
   `--skip-guards` or `--now`", and one `--why` gives the reason for both when both are passed. `check_runs` copies
   `run-checks`' stderr to its own stderr as it arrives, so the waiting lines reach the caller while it waits. It
   reads stdout as one JSON document, as today.

   `gate`'s inline repository gates, hooks, `prepare` steps and git calls don't claim. They are short, and `prepare`
   runs inside a claim that is already held.
10. **The lead sees the machine where it already looks.**
    - `lead-wait` adds a `machine` object to every wake: capacity, used, the running claims, the waiters, and the last
      five override entries from the history. With an unusable ledger it carries `{unavailable: <reason>}`.
    - It also wakes when a claim whose `delivery` is the lead's own has waited longer than `--claim-wait-seconds`
      (default 900). This happens once per waiting stretch, tokened like a blocked lane, so a restart doesn't wake on
      it again. Another delivery's long wait never wakes it. The lead decides whether to admit, and nothing is
      admitted automatically.
    - The built-in page and the markdown renderer gain a **Machine** section beside peers. It shows capacity and live
      pressure, then running claims (label, repository, delivery, slots, since, expected end), then waiters in order,
      then the last five overrides with their reasons.
    - The section is read live at render time, the way the merge slot's holder is read today. It is left out when the
      `claims/` folder is empty and the history holds no override. An unusable ledger shows as unavailable.
    - The queue writes nothing to `meta.checking.now` while it waits for capacity, because a lead reads a peer's
      `meta.checking.now` before pushing as "checks in progress". The queue's wait shows in the ledger, in its log
      line on stderr, and in the views.
    - No new journal kinds are added. Overrides inside a delivery use `decision.recorded`. Waits and admissions live in
      the machine history, which covers every repository.
11. **Briefs name the primitives, not a policy.** The lane brief and each lens round brief from `review-prepare`
    say three things: that `run_delivery.py claim -- <argv>` is the default way to start a heavy command, that
    `machine.py status` shows the machine, and that `--now --why` is how to step off. They say nothing about when to
    run what.
12. **Rulings on what exists.**
    - **Suite slot script: replaced.** The ledger covers what it did: one full suite at a time becomes slot
      accounting, and waiting on a quiet-machine lease becomes an exclusive claim. The script is outside this
      repository, so retiring it is an operator step, taken once Athena claims (follow-up V26-3684).
    - **Quiet-machine lease (`ATHENA_QUIET_MACHINE_LEASE`): replaced** by `machine.py run --exclusive --label <why>
      -- <argv>`. That covers a benchmark that needs a quiet machine. Athena moves over in V26-3684. Until then, Athena's
      variables keep working, because this plan changes nothing in Athena.
    - **Shared merge slot (V26-3318): kept.** It guards a base, not capacity. Decision 8 sets its order against
      claims.
    - **Reader checks before any round (V26-3592): built on.** That ticket is in Backlog and unchanged by this plan.
      The checks it proposes run through `run_delivery.py check`, and the lens checkouts it prepares run their checks
      the same way, so they claim with no change to its scope.
13. **One module, standalone at its edges.** The new `skills/orchestrate-work/scripts/machine.py` holds the ledger,
    the admission, the cost record, the capacity, the measured runner (`wait4`) and the CLI (`status`, `run`,
    `admit`, `capacity`).
    - It imports `file_lock`, `machine_reading`, `now` and the atomic writer from `delivery_state.py`, and owns the
      `holding_slot()` marker itself, so `merge_slot` is unchanged. `delivery_state.py` imports `machine` only inside `wait_for_lead`, so no import cycle
      forms.
    - `merge_queue.py` and `run_delivery.py` import it from the same installed release they run from.
14. **Tests never touch the real machine directory.**
    - `tests/__init__.py` assigns `AGENT_SKILLS_MACHINE_DIR`, overwriting any value already set (not `setdefault`), to
      a fresh directory under `tempfile.gettempdir()`, once per test process.
    - A fixture queue or command that a test starts inherits that variable, so it uses the test's own ledger.
    - Every test whose assertion reads the ledger, the costs, the capacity or the Machine section points
      `AGENT_SKILLS_MACHINE_DIR` at its own fresh directory in `setUp` (`mock.patch.dict`) and writes an explicit
      `capacity.json`. Its result then doesn't depend on test order, on `unittest discover` running everything in one
      process, or on `os.cpu_count()`. That covers `tests/test_machine.py`, the new rows in
      `tests/test_merge_queue.py` and `tests/test_run_delivery.py`, and every output-pinning test in
      `tests/test_lead_wait.py` and `tests/test_orchestration_render.py`.
    - A real `fullHealth` run of the suite claims in the real ledger from `full-health-run`. The suite inside it uses
      its own ledgers, so the suite never queues behind its own claim.
15. **Release.** This is a runtime change, so it goes out first as a candidate (`-rc.N`), is applied in this
    repository's own deliveries, and is then promoted. It is additive (a minor version). No delivery record shape,
    binding field or journal kind changes. The machine files' shapes (`machine-claim/1`, `machine-capacity/1`,
    `machine-costs/1`, `machine-history/1`) are added to `schemas.json`.
16. **Catalog entries and profile descriptions.** Following AGENTS.md:
    - V26-3680 adds one sentence to orchestrate-work's `degradedBehavior`: an unusable machine directory runs work
      unclaimed and says so, and without `wait4` slots come from `--slots`.
    - V26-3682 adds one to run-delivery's: claiming commands run unclaimed when the ledger is unusable.
    - V26-3683 adds one to orchestrate-work's: the Machine section and `lead-wait`'s `machine` object show an unusable
      ledger as unavailable.
    - V26-3681 adds none, because what it adds degrades as V26-3680's sentence says.
    - **Profile descriptions.** V26-3680 adds one sentence for the whole plan to the `description` of each of the five
      profiles that select orchestrate-work: the claims ledger, measured costs and capacity, heavy commands claiming
      and waiting by default, recorded overrides, and the Machine view. Each description is one line, and a release
      carries all four stories, so one writer avoids a conflict across lanes without describing behaviour no release
      has.
    - Each entry is one line of `catalog.json`, so lanes 1 and 2 edit different lines. V26-3683 edits orchestrate-work's
      line only after V26-3680 has merged, and V26-3681 doesn't touch it.
17. **Plan review has no round bound.** It runs until its lenses align, or until the operator ends it.

## Out of scope

- Changing Athena. V26-3684 is filed in Athena's project, related to V26-3638, to make Athena's `full-health` checks and
  benchmark claim through the installed `machine.py`, and to drop `ATHENA_SUITE_SLOT_LOCK` and
  `ATHENA_QUIET_MACHINE_LEASE`. Retiring `suite-slot.sh` is an operator step after V26-3684.
- The line map (`delivery-views`). V26-3685 is filed, related to V26-3638, to draw the Machine section on it from the same reading.
  It is left out here because the hosted viewer patches `line-map.js` at fixed hooks, a cost V26-3592 records.
- Memory as a cost dimension. Swap and free disk stay as information beside the ledger (Decision 7).
- Admission from live load. Load reflects runs that don't claim, and acting on it would bring back the fixed rule
  the ticket rules out. It is shown, not acted on.
- Choosing a run's worker count. The tool says how many slots are free, and the agent sizes its run.
- Wrapping `scripts/run-tests.py` itself. It is this repository's script, not a released one. Its callers claim.
- Any change to `meta.checkHistory`, to the merge slot's files, or to the TypeScript harness.

## Approval Handoffs

- **Decision 2 was confirmed by the operator on 2026-11-20:** the ledger is per machine, not beside the git common
  directory.
- Approved by the operator on 2026-11-20, after round 5 aligned at 23c67ccc. The four stories are filed under
  V26-3638 as V26-3680 to V26-3683, and their keys replaced the plan's placeholder keys. Later that day they moved
  under the epic V26-3689, and V26-3638 closed as Done. The follow-ups are filed as
  V26-3684 and V26-3685, each related to V26-3638 and to the epic, and blocked by the story it builds on.
- The operator chooses the delivery base, `main` or an integration branch, at `lead-setup`.
- V26-3573 (In Progress) changes the lead-wait fixture tests. V26-3683 starts on a base that has V26-3573 merged.

## Execution posture and sensors

The posture is test-first. Each story's sensors:

- its touched test modules, through `.venv/bin/python -m unittest <modules>`;
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`;
- `tests.test_linear_qualification` and `tests.test_registry`, the binding's guards, which run at `gate`;
- the full suite with `--workers 4 --budget tests/suite-budget.json`, run by the queue and by `fullHealth`, not by
  lanes.

**Test cost.**
- Ledger, admission and estimate tests run in-process, with an injected clock and sleep.
- In V26-3680, the rows that need real processes share one class:
  - Scenario 6, a child holding a claim, killed;
  - Scenario 20, the measured runner with a grandchild;
  - Scenario 21, a nested claim;
  - Scenario 14's subprocess rows.
- That class builds its fixture once, takes about 0.3 s of CPU per measured row, and is `run_quietly`, because it
  waits on real processes exiting.
- Queue and `run_delivery` scenarios reuse the fixture classes `tests/test_merge_queue.py` and
  `tests/test_run_delivery.py` already build. Only the cross-process rows (Scenarios 19 and 22) start one extra
  process each.
- No class needs `run_serially`.

**Shared resources.** Each test's machine directory is its own (Decision 14). No ports. The merge slot is used only by
existing fixtures, under their own git common directories.

## Scenarios

Capacity is always set explicitly, by writing `capacity.json` in the test's own directory.

| # | Story | Given | Then |
| --- | --- | --- | --- |
| 1 | V26-3680 | capacity 4; claim A of 4 running | claim B of 1 waits. Its stderr line names A's label, delivery, slots and expected end, and says 0 slots are free. |
| 2 | V26-3680 | capacity 4; A of 2 running; B of 3 waiting; C of 1 arrives | C waits behind B (no backfill), although 2 are free. |
| 3 | V26-3680 | as 1, then A is released | B is admitted with `admittedBy: capacity`. The history records the wait's length. |
| 4 | V26-3680 | as 1, with B run `--now --why x` | B runs at once with `admittedBy: now, why: x`. The history records an override entry. `--now` alone exits 2. |
| 5 | V26-3680 | as 1; another process runs `admit <B> --why y` | B is admitted with `admittedBy: admit`, and the history names y. |
| 6a | V26-3680 | a child process takes a claim of 4 and is killed with SIGKILL | the next read removes its file, and a waiting claim is admitted. |
| 6b | V26-3680 | a claim file whose lock nobody holds, whose `pid` is the test's own live pid | the next read removes it. |
| 6c | V26-3680 | a claim file whose lock another open file description holds, whose `pid` names no process | it is kept and counted in `used`. |
| 7 | V26-3680 | capacity 4; nothing running and nothing waiting; claim of 6 | admitted `alone`. Later claims wait until it is released. |
| 7b | V26-3680 | capacity 4; A of 1 running; B of 1 waiting; then A is released at the moment claim C of 6 arrives | B is admitted before C. C is admitted `alone` only after B is released. |
| 8 | V26-3680 | no cost record; claim `--slots 3` | slots 3, `estimate.from: none`. |
| 9a | V26-3680 | capacity 8; six passed runs, oldest first, with wall seconds 100, 200, 300, 900, 1000 and 5000, and CPU seconds 100, 200, 300, 1800, 4000 and 20000; then one failed run of 10000 s wall and 80000 s CPU | the passed list keeps the last five. The estimate is `{seconds: 900, slots: 2, from: measured}`: the median of `ceil(cpu/wall)` over 1, 1, 2, 4 and 4 is 2, while their mean would make 3. The failed run is stored in the failed list and moves nothing. |
| 9b | V26-3680 | capacity 2; five passed runs each with `ceil(cpu/wall)` = 4 | slots is clamped to 2. |
| 9c | V26-3680 | five passed runs, every `cpuSeconds` null; claim `--slots 3` | `seconds` is measured, slots is 3, `from: measured`. |
| 9d | V26-3680 | five passed runs, each 600 s wall and 600 s CPU; then six failed runs, each 10000 s wall and 80000 s CPU | the estimate stays `{seconds: 600, slots: 1, from: measured}`. |
| 13 | V26-3682 | `fullHealth` starts while capacity is full, with an injected clock advanced past an hour | it is still waiting, with no `gave-up` entry, and once admitted it runs as before. The supervisor's command line passes no `--wait-seconds`. A second supervisor pass while it waits starts no second `full-health-run`, `full_health_running` reports the waiting run, and the ledger holds exactly one `fullHealth` claim. |
| 14a | V26-3680 | any test process | `AGENT_SKILLS_MACHINE_DIR` is set, under `tempfile.gettempdir()`, and differs from the real default. |
| 14b | V26-3680 | a subprocess started with `AGENT_SKILLS_MACHINE_DIR` set to a decoy path imports the `tests` package | it sees the temp directory, not the decoy. |
| 14c | V26-3680 | a child started the way the queue fixtures start theirs calls `machine.py`'s resolver | it returns the test's directory. |
| 14d | V26-3680 | the resolver, with `AGENT_SKILLS_MACHINE_DIR` and `XDG_STATE_HOME` both set; then with only `XDG_STATE_HOME`; then with neither | it returns `AGENT_SKILLS_MACHINE_DIR`; then `$XDG_STATE_HOME/agent-skills/machine`; then `~/.local/state/agent-skills/machine`. |
| 17 | V26-3680 | capacity 4; exclusive claim E waiting behind A of 1; then claim C of 1 arrives | E is admitted once A is released. C waits until E is released. |
| 18 | V26-3680 | `--wait-seconds 5` while full | exits 75 after 5 s, runs nothing, names the holders, and records `gave-up`. |
| 20 | V26-3680 | `machine.py run --kind run --label probe -- <argv>`, where argv starts one grandchild and both burn about 0.3 s of CPU | its `costs.json` passed entry has non-null `cpuSeconds` of at least the grandchild's own CPU time. A second claim of the same label gets `estimate.from: measured`. |
| 21 | V26-3680 | capacity 4; `machine.py run --slots 4 -- <argv>`, where argv itself runs `machine.py run --slots 4 -- <marker>` | the inner command runs at once under the outer claim, prints `running under claim`, and the marker appears. `used` never exceeds 4. Once the outer claim is released, a new claim naming that id in the variable claims as usual. |
| 23 | V26-3680 | a run that exits 1; a run stopped by the runner's timeout; a run interrupted by `KeyboardInterrupt`; each with a waiter behind it, in one process | afterwards `claims/` holds no file for it, `used` is back to its prior value, and the waiter is admitted. |
| 24 | V26-3680 | a machine directory that can't be created (a file at its path) | `machine.py run` prints `machine ledger unavailable` and runs argv. |
| 24a | V26-3680 | capacity full; a claim file is removed between the listing of `claims/` and its read | it is skipped, and the new claim still waits. |
| 24b | V26-3680 | capacity full; `ledger.lock` is held by another open file description for less than the lock timeout | the claim is delayed, then waits, and doesn't print `unavailable`. |
| 24c | V26-3680 | capacity full; a malformed claim file whose lock another open file description holds | the ledger isn't unavailable, and the new claim still waits. |
| 25 | V26-3680 | 1,005 history entries are written | the file keeps the last 1,000. |
| 10 | V26-3681 | capacity 4; a `run` claim of 4 from another process running; a queue turn starts | the turn's claim is `waiting` in the ledger and its stderr line names the run claim's label, delivery, slots and expected end. The base's slot has no holder file and isn't locked, and `meta.checking.now` is unchanged. Once the run claim is released, the turn is admitted, takes the slot and runs its checks. |
| 11a | V26-3681 | capacity 8; `parallelChecks` 2; a lead selection of three checks with measured slots 3, 1 and 2 | the turn's claim taken before the slot is exactly 6. |
| 11b | V26-3681 | as 11a, with no lead selection | the pre-claim is computed over the binding's `alwaysRun` and `fallback` checks. |
| 11c | V26-3681 | capacity 6 and full; under the slot the queue selects checks whose formula gives 8 | the turn claim grows to 8 with `admittedBy: slot`, and `used` is exactly 8 (not 14). |
| 11d | V26-3681 | under the slot the queue selects one check with slots 1 | the turn claim shrinks to 1, and `used` drops by the difference. |
| 22 | V26-3681 | capacity full; process A, the queue fixture, holds the base's merge slot; a separate process B makes a 1-slot claim | B stays `waiting` with no `admittedBy`. A claim made in A under the slot is admitted `slot`. |
| 31 | V26-3681 | capacity 4 and full with the turn's claim of 4; the turn's check argv runs `machine.py run --slots 4 -- <marker>` | the marker appears with no wait, and `used` never exceeds the turn claim. The inner command prints `running under claim <the turn's claim id>`, and its cost entry appears in the test directory's `costs.json` under `(run, <label>)`. With `HOME` pointed at a fresh temporary directory for the row, no machine directory is created at the resolver's default path. The check's environment digest and its record's `names` equal those taken without the runner-owned variables. |
| 32 | V26-3681 | one serve process; turn 1 has a failing check; and, in a second row, a turn that raises a Stop while holding the slot | afterwards `claims/` holds no file for that turn's claim and `holding_slot()` is clear. With capacity then filled from another process, turn 2's pre-claim is `waiting` with no `admittedBy`, and is admitted `capacity` only once that claim is released. |
| 33 | V26-3681 | the turn's claim is admitted, but its slot wait ends in `queue_locked` | the turn keeps its claim through serve's retry, so no later arrival is admitted ahead of it, and releases it when the turn ends. Four more rows, each after a `queue_locked` retry that kept the claim, cover every pass that takes no turn: the lead sets `meta.hold`; the base is marked as awaiting repair; the candidate is dequeued so nothing is queued; a setup step stops. In each, by the end of the next pass `claims/` holds no file for that turn, and a claim from another process is admitted `capacity`. The hold, repair and dequeue rows share one fixture. |
| 26 | V26-3681 | `run-checks --now --why w` while full; then `run-checks --wait-seconds 1` while full | the first runs at once with `admittedBy: now, why: w`. The second exits 75 with no record on stdout. |
| 12 | V26-3682 | a lane runs `run_delivery.py claim --now --why z -- <argv>` while full | argv runs. The delivery journals `decision.recorded` with z, and the history matches it. |
| 19 | V26-3682 | capacity 4; a queue fixture's turn claim of 4 running; `run_delivery.py claim --slots 4 -- <argv writing a marker>` without `--now` | the marker is absent and the claim is `waiting`. Its line names the turn claim's label, delivery, slots and expected end. The marker appears after the turn claim is released. |
| 27 | V26-3682 | `run_delivery.py check` while the machine is full | the waiting line, naming the holder, reaches the caller's stderr before the check starts. Stdout is still the one JSON document. |
| 28 | V26-3682 | capacity equal to one guard's measured slots; `lead-push` with that guard | the guard runs, the push completes, and `used` never exceeds capacity. No claim is made by the `lead-push` process itself, and the history holds the guard's own claim (`kind: check`, `subject: <guard id>`). |
| 29 | V26-3682 | `gate --now --why r` while full | the guards run at once. `--now` without `--why` is refused. `--why` without `--skip-guards` or `--now` is refused with the amended message. |
| 30 | V26-3682 | `run_delivery.py claim -- run_delivery.py check …` at capacity equal to the check's slots | the check runs under the outer claim with no wait. |
| 15a | V26-3683 | a claim of this delivery has waited 901 s with `--claim-wait-seconds 900` | `lead-wait` wakes once, with the claim in `machine`. A restart that carries the token stays quiet while it waits. |
| 15b | V26-3683 | as 15a, but the claim's delivery is another one | `lead-wait` doesn't wake. |
| 16a | V26-3683 | a machine directory with two running claims, one waiter and seven overrides | the page and the markdown show the Machine section with both claims, the waiter and the last five overrides. |
| 16b | V26-3683 | an empty machine directory; and one whose `claims/` is empty but whose history has a wait and no override | neither view shows the section. Existing snapshots are unchanged. |
| 16c | V26-3683 | an unusable machine directory | the section says the ledger is unavailable, and `lead-wait`'s `machine` is `{unavailable: …}`. |

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3680 | `skills/orchestrate-work/scripts/machine.py`, `skills/orchestrate-work/references/schemas.json`, `tests/test_machine.py`, `tests/__init__.py`, `profiles/`, `tests/test_orchestration_adopter.py`, `catalog.json` |
| 2 | 1 | V26-3681 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py` |
| 2 | 2 | V26-3682 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `catalog.json` |
| 2 | 3 | V26-3683 | `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`, `docs/own-deliveries.md`, `tests/test_lead_wait.py`, `tests/test_orchestration_render.py`, `catalog.json` |

`provenance.lock.json` and `catalog.json` are shared:
- Each lane runs the provenance writer. A conflict on the lock is resolved by the AGENTS.md rule: take either side,
  rerun the writer, then validate the registry. Only V26-3680 adds a listed output (`machine.py`).
- In `catalog.json`, lanes edit different lines, as Decision 16 says.

## Stories

### V26-3680. Keep a machine ledger of claims, measured costs and a capacity

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/machine.py`,
`skills/orchestrate-work/references/schemas.json`,
`tests/test_machine.py`, `tests/__init__.py`, `profiles/`, `tests/test_orchestration_adopter.py` and
`catalog.json`.

Scope:
- Decisions 2 to 7, 13, 14 and 16;
- the process-local `holding_slot()` marker;
- the `machine.py` CLI: `status`, `run`, `admit` and `capacity`, with `--json` on `status`.

Scenarios 1 to 9d, 14a to 14d, 17, 18, 20, 21, 23 to 24c and 25.

Pins to re-derive:
- `machine.py` is added to the five profiles that list `region_ownership.py` (`core`, `delivery-views`, `jira`,
  `linear` and `run-delivery`);
- it is added to the script list at `tests/test_orchestration_adopter.py:982`;
- it is added to `provenance.lock.json` as a new output;
- the five profiles' descriptions gain Decision 16's sentence.

Criteria:
- the scenarios pass;
- `status` prints capacity and pressure;
- no admission reads load;
- no fixed rule replaces the agent's judgement.

### V26-3681. Claim for the queue's turn before the merge slot

Lane 1, tier 2, after V26-3680. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py` and
`tests/test_merge_queue.py`.

Scope:
- Decision 8 for the queue turn: one claim before the slot, resized under it inside `holding_slot()`, with cost
  recorded per check id;
- Decision 9 for `run-checks`: a claim around each check, `--now --why`, `--wait-seconds`, and waiting lines on stderr
  only;
- passing `AGENT_SKILLS_MACHINE_CLAIM` through, and the runner-owned machine variables into every check and
  `prepare` environment (Decision 4);
- keeping a turn's claim across a `queue_locked` retry;
- writing nothing to `meta.checking.now` while waiting.

Scenarios 10, 11a to 11d, 22, 26 and 31 to 33.

Criteria:
- the scenarios pass;
- existing queue tests pass unchanged, with capacity left at the default and each test's own empty ledger;
- no fixed rule replaces the agent's judgement.

### V26-3682. Claim for fullHealth, lead-suite and any heavy run, and name the claim in the briefs

Lane 2, tier 2, after V26-3680. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery.py`,
`tests/test_run_delivery_supervisor.py` and `catalog.json`.

Scope:
- `run_delivery.py claim [--slots N] [--label L] [--exclusive] [--now --why W] [--wait-seconds S] -- <argv>`, which
  fills in the delivery and story from the lane's context and journals `decision.recorded` on `--now`;
- claims for `fullHealth`, held in `full-health-run`, and for `lead-suite`;
- `--now` on `check`, `gate`, `lead-push` and `lead-suite`, with the amended `--why` rule;
- `check_runs` copying `run-checks`' stderr live and passing `AGENT_SKILLS_MACHINE_CLAIM` through;
- `lead-push` taking no claim of its own;
- the brief text in the lane brief and the lens round brief;
- run-delivery's catalog sentence.

Scenarios 12, 13, 19 and 27 to 30.

Pins to re-derive:
- the `--why`-without-`--skip-guards` message assertions in `tests/test_run_delivery.py`;
- any brief-text assertion there that compares a whole brief.

Criteria:
- the scenarios pass;
- a corpus pin holds the brief sentence naming `claim` and `--now --why`;
- an absence pin shows the brief carries no rule about when to run what. It searches the lane brief and the lens
  round brief, by meaning, for these phrases and their synonyms: "only one", "one at a time", "never overlap", "must
  wait", "wait for", "do not run", "avoid running" and "before running";
- no fixed rule replaces the agent's judgement.

### V26-3683. Show the machine in the lead's wait and the delivery views

Lane 3, tier 2, after V26-3680. Test-first. Starts on a base with V26-3573 merged. Files:
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/render_state.py`,
`skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`,
`docs/own-deliveries.md`, `tests/test_lead_wait.py`, `tests/test_orchestration_render.py` and `catalog.json`.

Scope:
- Decision 10: the `lead-wait` `machine` object and `--claim-wait-seconds`, and the Machine section in the built-in
  page and the markdown;
- the guide's account of the primitives, the defaults and the overrides;
- `docs/own-deliveries.md`, which covers setting capacity and running an exclusive claim, and says that the suite slot
  is retired once V26-3684 lands;
- V26-3683's catalog sentence.

Scenarios 15a, 15b and 16a to 16c.

Pins to re-derive:
- whole-payload assertions on `wait_for_lead` results in `tests/test_lead_wait.py`, each now reading its own machine
  directory with an explicit capacity (Decision 14);
- page snapshots in `tests/test_orchestration_render.py` for a non-empty machine directory only. An empty directory
  renders as before.

Criteria:
- the scenarios pass;
- existing render snapshots are unchanged when the machine directory is empty;
- no fixed rule replaces the agent's judgement.

### V26-3693. Serve the hosted page in the same fonts as the local live view

Lane 4, tier 1, not blocked. Added mid-delivery: The operator added it on 2026-11-20: the hosted page falls back to Georgia and the system sans because its policy and asset build carry none of the local view's fonts.
