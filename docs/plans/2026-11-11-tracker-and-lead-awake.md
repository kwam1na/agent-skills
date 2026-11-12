---
reviewSlug: tracker-and-lead-awake
---

# Keep the live view at one address, and every process on a Node the runtime accepts

Epic V26-3306. Of its five items, three were settled elsewhere: V26-3269 is done, V26-3261 was cancelled, and V26-3270 moved to
V26-3406. This plan delivers the remaining two:

- V26-3260: check every process's Node against the runtime's minimum.
- V26-3262: give the live view one address that survives supervisor restarts.

Both come from V26-3169's friction log. V26-3169's supervisor ran its queue under Node 20 while `doctor` reported Node 22 as fine. Its
live view moved to a new port with every restart.

The delivery lands on its own integration base, `integrate/tracker-and-lead-awake`, cut from `origin/main` at `fa1f09f`, and not on
`main`. Both items edit `skills/run-delivery/scripts/run_delivery.py` and `tests/test_run_delivery_supervisor.py`, which V26-3406's lane-2
is changing on `main` now. The integration base keeps the two deliveries apart until V26-3406 closes. One reviewed pull request then
brings this work to `main` (Decision 1).

Each story's criteria are written in full in its section. On the operator's approval, the tracker items' Acceptance Criteria are
replaced with them word for word, so `lead-setup` drafts them from the tracker snapshot.

## Finish line

- Each item is merged to `integrate/tracker-and-lead-awake` through the merge queue, or closed with a recorded reason.
- The full local suite passes on macOS with Python 3.11 at the integration base's last merged commit.
- After V26-3406 closes, one reviewed pull request merges the integration base into `main`, as Decision 1 describes:
  - its CI passes on the Ubuntu job;
  - the full local suite passes on macOS with Python 3.11 at its head;
  - the lead merges it, under the operator's grant of 2026-11-11;
  - `main`'s CI then passes on the Ubuntu job and `cut-release` at the merge commit, with the run linked on the epic.
- No finding is deferred: every finding any review in this delivery raises, at any severity, is fixed in the story that found it, and no
  follow-up item is filed for it.

## Out of scope

- A release. These changes reach this repository's own deliveries at the next batch release, which is cut separately.
- Sharing the live view beyond the lead's machine (V26-3070). The view still listens on the loopback address only.
- A port declared in the binding. The port is derived and recorded (Decision 2), so `orchestration-binding/1` gains no member.
- Checking the Node of processes the delivery didn't start: a lane's in-app servers, editors, or the operator's shells.
- Choosing or installing a Node. The checks refuse and name the fix; they never rewrite `PATH` or pick an nvm Node for a child.
- Windows, which is not validated (AGENTS.md).

## Decisions

1. **The integration base and its way back to `main`.**
   - The binding gains `"bases": ["main", "integrate/*"]`, committed only on the integration base (`439eea9`), so `main` and V26-3406's
     running delivery are untouched. `main` gains the entry when the integration base merges, and from then on serves any `integrate/*`
     base.
   - The delivery file names the base with `lead-setup --base integrate/tracker-and-lead-awake`. The plan, delivery file and lane brief
     are committed on the integration base, and lanes read them from it on the remote.
   - The lead never merges `main` into the integration base while stories are open, since that moves every lane's base.
   - Once V26-3406 closes, the lead cuts `sync/v26-3306` from the integration base, merges `origin/main` into it, and resolves the
     conflicts there. Expected conflicts: `run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `provenance.lock.json`, the skill
     docs. The lock is resolved by the writer, as AGENTS.md says.
   - The lead then runs the full suite on macOS with Python 3.11 and opens the pull request from `sync/v26-3306` into `main`. It is
     reviewed with the delivery's lenses, and the lead merges it under the operator's grant.
   - If V26-3406 is still open when both stories merge here, the delivery waits on that finish line, and the record shows it.
2. **The live view's port is derived, then recorded.**
   - The port is derived from the repository's top-level path and the delivery key: the first eight bytes of the sha256 of
     `<path>\0<key>`, read as a big-endian integer, modulo 10000, plus 20000. The same delivery in the same checkout always asks for the
     same port. Two deliveries can still derive the same port; the fallback below handles that.
   - The supervisor records the port it bound in `live.json` in the delivery's state directory: `port`, the view's `pid`, the resolved path
     (symlinks followed) of the `render_state.py` it runs, and when it started. A restart asks for the recorded port before the derived one.
   - When that port is held by a process that isn't this delivery's view, the supervisor binds a free port instead and records it. It
     says so on its own output and in the next status line, naming the port it wanted and who holds it (`port_holders`). Because the new
     port is recorded, later restarts keep it.
   - `RUN_DELIVERY_LIVE_PORT=0` makes the view bind a free port, for tests and for a lead who wants that.
3. **The live view outlives the supervisor, until the delivery closes.**
   - The supervisor starts the view in a session of its own (`start_new_session`), not in its process group. A stopped or killed
     supervisor, including a signal to its whole group, leaves the view serving.
   - A starting supervisor first looks for a view of this delivery already serving: `live.json`'s pid alive, listening on its recorded
     port (`port_holders`), and started from the same `render_state.py` path as the current generation's. It adopts that view rather than
     starting another.
   - A live pid that is not listening on the recorded port is not adopted and not signalled; the supervisor starts a new view and records
     it. A view started from another generation's `render_state.py` is stopped by its recorded pid and replaced by one from the current
     generation, on the same port.
   - The view stays read-only: it serves the record and changes nothing.
   - The delivery's close stops the view last, after the final render and the archive, by its recorded pid. That is the supervisor's own
     close-out and `lead-close` alike. `lead-init --replace` stops the replaced delivery's view with its supervisor. Nothing stops it by
     pattern.
4. **The view says why the supervisor is down.**
   - The supervisor writes `supervisor.json` in the state directory: its pid, `running` or `stopped`, when, and why. Each reason is
     written by what stops it:
     - `close`: the supervisor's own close-out, before it stops the view;
     - `replaced`: `lead-init --replace`, which writes it before it signals the supervisor, so it lands in the archived state directory;
     - `held`: `lead-close`'s stop request, which carries the reason to the supervisor it asks to stop after its pass;
     - `signal`: any other SIGTERM, SIGINT or SIGHUP the supervisor receives, naming it.
   - `render_state.py live` gains `--status-file PATH`, generic to orchestrate-work. It re-reads the file on each render.
   - While the file says `running` and its pid runs, the page shows no banner. While it says `stopped`, or names a pid that no longer
     runs, the page shows a banner: the supervisor is down, since when, and why. A dead pid with a `running` mark shows as "stopped without
     recording why", which is what a killed supervisor leaves. The banner names `lead-run --resume`.
   - Without `--status-file` the view renders as today.
5. **Every lead command's status line names the address while the view serves, also when the supervisor is down.**
   - Running: as today, `supervisor: running (pid N); live view <address>`.
   - Down with the view serving: `supervisor: down (<why>); live view <address>; restart it with ... lead-run --resume`, reading the
     address from `live.json`.
   - Down with the view gone too: the same line without an address.
   - When `closed_status` has a line (the delivery closed, or a close stopped partway), the status line is that line, as today; the
     down forms apply only when it has none.
   - The line is `supervisor_line`, which every lead command but `lead-setup` and `lead-init` already prints, so one change reaches all.
6. **"The Node a process uses" is the first `node` on that process's `PATH`.** Children resolve `node` through `PATH`, so an nvm Node
   further down doesn't count.
   - One function compares versions, numerically per component, for `doctor` and every refusal below.
   - `doctor`'s Node row is `wrong` when the Node first on `PATH` is below the minimum and `missing` when there is no `node` on `PATH`,
     even with an nvm Node that meets it. The row then names that nvm Node's `bin` folder as the entry to put first. `load_harness` keeps
     its own nvm lookup, since it runs Node itself.
   - `lead-run` refuses when the Node first on its `PATH` is below the runtime's `nodeMinimum`, or when there is none, naming the `PATH`
     entry, the version and the minimum. It refuses before it takes the lock, stops any recorded process (`--resume`'s orphan sweep
     included) or starts a child.
   - `lead-init`, and every command that starts a supervisor detached, runs the same check before it writes anything, so the refusal
     reaches the lead's shell rather than a log.
   - A generation without a runtime asks for no Node, as today.
7. **`lane-start` checks the lane's Node and Python.**
   - Node: as Decision 6, against the runtime's `nodeMinimum`.
   - Python: the interpreter running the tool and the first `python3` on `PATH` must each be 3.11 or newer, the minimum `doctor`
     already holds. The interpreter's version is read through one function, so a test can stand in a version.
   - It refuses before it creates the checkout or records a start, naming what it found and the fix.
8. **The supervisor records its Node, and `doctor` reports it.**
   - At start, the supervisor writes the Node first on its `PATH`, path and version, into its lock record.
   - `doctor --delivery`'s supervisor row reports it beside the pid and address.
   - The row is `wrong` when that Node is below the minimum. A supervisor started by an older generation may have no Node recorded; the
     row then says so, as `warn`.
9. **The queue records each check's Node, and marks one below the minimum.**
   - Each check result entry in the record gains an optional `node` member: the version of the first `node` on the `PATH` the check ran
     with, after the binding's and its profile's environment. It is `null` when there was none.
   - When the runtime declares a `nodeMinimum` and `node` is below it, the entry also carries `nodeBelowMinimum: true`. Otherwise,
     including a `null` `node`, the entry has no such member. The check's own
     verdict is unchanged: the mark explains a failure, it doesn't cause one.
   - orchestrate-work's state schema gains both members as optional, `node` a string or `null` and `nodeBelowMinimum` a boolean, so
     existing records still validate.
   - The live view shows the Node in the check's detail, and the mark beside it.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3260, V26-3262 | `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_merge_queue.py`, `tests/test_orchestration_render.py`, `tests/test_orchestration_state.py`, `tests/test_registry.py`, `catalog.json`, `profiles/`, `provenance.lock.json` and `docs/solutions/` |

One lane, because both items change `run_delivery.py`, the supervisor and its tests. V26-3260 goes first. V26-3262 rebuilds how the
supervisor starts the view, and builds on the lock record V26-3260 extends.

## Stories

### V26-3260. Check every process's Node against the runtime's minimum: lanes, the supervisor and the queue

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/references/adopting.md`, `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_merge_queue.py`, `tests/test_orchestration_render.py`, `tests/test_orchestration_state.py`, `catalog.json`, `profiles/` and
`provenance.lock.json`.

Acceptance criteria:

1. With a Node below the runtime's `nodeMinimum` first on `PATH`, even with an nvm Node that meets the minimum, `lead-run` exits non-zero
   before taking the lock or starting a child. Its message names the `PATH` entry, the version found and the minimum.
2. With no `node` on `PATH`, even with an nvm Node that meets the minimum, `lead-run` refuses the same way, saying none was found.
3. With a Node below the minimum first on `PATH`, even with an nvm Node that meets it, `lead-run --resume` exits non-zero before it stops
   any recorded process or starts a supervisor. A view whose pid `live.json` records still answers afterwards.
4. With a Node below the minimum first on `PATH`, even with an nvm Node that meets it, `lead-init` refuses before it writes the record.
5. `lane-start` refuses, before creating the checkout or recording a start, naming the version, the minimum and the fix:
   - with a Node below the minimum first on `PATH`, even with an nvm Node that meets it;
   - with `python3` below 3.11 first on `PATH`;
   - with the interpreter running the tool reporting a version below 3.11.
6. With a Node exactly at the minimum first on `PATH`, `lead-run`, `lead-init` and `lane-start` make no Node refusal. Versions compare by
   number, so a Node `v100.0.0` meets a minimum `9.0.0`.
7. `doctor`'s Node row is `wrong` when the Node first on `PATH` is below the minimum, and `missing` when there is no `node` on `PATH`, even
   with an nvm Node that meets it. The row then names that nvm Node's `bin` folder as the entry to put first.
8. A supervisor started with a qualifying Node records that Node's path and version in its lock. `doctor --delivery`'s supervisor row
   reports them.
9. The supervisor row is `wrong` when the recorded Node is below the minimum, and `warn` when the lock records none.
10. A queue check whose profile environment puts a different `node` first on `PATH` records that node's version, not the queue's. With no
    `node` on that `PATH`, it records `null`.
11. A queue check whose recorded `node` is below the minimum carries `nodeBelowMinimum: true`, and its pass or failure is what the check
    itself returned. A check whose recorded `node` meets the minimum, one whose `node` is `null`, and any check under a generation whose
    runtime declares no `nodeMinimum` carry no `nodeBelowMinimum` member.
12. A record without `node` on its check results still validates. One whose `node` is neither a string nor `null` is refused.
13. The live view shows a check's Node in its detail, and the below-minimum mark when it is set.
14. A generation whose runtime declares no `nodeMinimum` makes no Node refusal in `lead-run`, `lead-init` or `lane-start`.

### V26-3262. Give the live view one address that survives supervisor restarts

Lane 1, tier 1, after V26-3260. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/references/adopting.md`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/test_run_delivery_supervisor.py`, `tests/test_orchestration_render.py`, `catalog.json`, `profiles/` and
`provenance.lock.json`.

Acceptance criteria:

1. Stopping the supervisor and resuming it with `lead-run --resume` keeps the same live view address, and the view is the same process.
2. With the supervisor run through a symlinked generation path (as `.agent-skills/current`), retargeting that symlink to another
   generation and resuming replaces the view, keeping the address; resuming without retargeting adopts the same view process.
3. The derived port for a fixed top-level path and key equals a literal computed from Decision 2's rule. The same key under a second fixed
   top-level path derives the literal Decision 2's rule gives for that path, which differs from the first. Deriving again with no `live.json` gives the same port.
4. With the supervisor and its view both stopped, and `live.json` recording a port other than the derived one, as after a fallback, a
   restart binds the recorded port.
5. With that port held by another process, the restart binds a free port, records it, and says on its output and in the next status line
   which port it wanted and which process holds it.
6. With `live.json` naming a pid that is alive but not listening on the recorded port, a restart starts a new view, doesn't signal that
   pid, and records the new view's pid.
7. Two deliveries in the same checkout each derive their port from their own key. When one's derived port is held by the other's view, it
   binds a free port and records it, as criterion 5 says.
8. While `supervisor.json` says `running` and its pid runs, the page shows no down banner. After the supervisor stops on a named signal,
   the same view's page shows the banner, naming the signal and when.
9. With the supervisor's process group killed by `SIGKILL` (`os.killpg` on its pid), the view's recorded pid still answers on the same
   address. Its page's banner says the supervisor stopped without recording why, and names `lead-run --resume`.
10. `supervisor.json` records who stopped the supervisor:
    - after the supervisor's own close-out, `close`;
    - after `lead-init --replace`, `replaced`, in the archived state directory;
    - after `lead-close`'s stop request, `held`;
    - after a SIGTERM, `signal`, naming it.
11. The status line names the live view's address while the view serves, both with the supervisor running and down. With the supervisor
    and its view both stopped in a delivery that is not closed, it reads `supervisor: down (<why>)`, names `lead-run --resume`, and names
    no address. This holds for at least `lead-assign`, `lead-accept` and `lead-reconcile`. A closed or partway-closed delivery's line is
    unchanged.
12. The supervisor's own close-out and `lead-close` each stop the view last, by its recorded pid. The view was listening before; afterwards
    its pid is gone and nothing listens on its port.
13. `lead-init --replace` stops the replaced delivery's view.
14. `render_state.py live` without `--status-file` renders as it did before.

## Shared resources

- **Ports.** The supervisor tests start real views. They set `RUN_DELIVERY_LIVE_PORT=0` unless the test is about the derived or recorded
  port. A test that binds a derived or recorded port, or holds a port to force the fallback, marks its class `run_serially = True`.
- **Processes.** Tests stop only the pids they recorded (AGENTS.md). The view now runs in its own session, so every test that starts a
  supervisor stops its view by `live.json`'s pid in teardown.
- **Node on `PATH`.** Tests that need a too-old or missing Node build a `PATH` in their own temporary folder, with a stub `node` and
  `python3` that print a version. The rows that prove Decision 6 also set `HOME` to that folder, holding a stub
  `.nvm/versions/node/v<minimum>/bin/node` that meets the minimum, so an nvm Node is present and must be ignored. No test depends on the
  machine's nvm. The launcher rows that need a real Node 22 keep needing it first on `PATH` (the lead's machine has Node 20.19 as nvm's
  default).
- **Base-relative sensors.** `scripts/update-provenance.py --check` compares against the lock in the checkout, so it runs again after
  each sync with the base.

## Execution posture

`test-first` for both items. The lane works to the focused lane profile, with no full suite in a lane. The queue runs the selected checks
on the synced head, `fullHealth` runs after each merge, and the lead runs the full suite at the integration base's last merge and again
on `sync/v26-3306`.

Sensors:
- `scripts/validate-registry.py`;
- `scripts/update-provenance.py --check`;
- `tests/test_run_delivery.py`;
- `tests/test_run_delivery_supervisor.py`;
- `tests/test_merge_queue.py`;
- `tests/test_orchestration_render.py`;
- `tests/test_orchestration_state.py`;
- the full suite through `scripts/run-tests.py`.

## Approval handoffs

- The plan itself, before `lead-setup`.
- Replacing V26-3260's and V26-3262's Acceptance Criteria in Linear with this plan's, word for word.
- Pushing `integrate/tracker-and-lead-awake` to `origin`. The operator asked for the integration base on 2026-11-11, and lanes read
  the delivery from it on the remote.
- The grant to the lane: `push` and `draftPullRequests`, and the lead's `readyPullRequests`.
- The pull request from `sync/v26-3306` into `main`, and its merge. The operator granted the lead the merge on 2026-11-11, once the finish line's checks pass.

## Plan review

Every lens raises every finding it can state against this plan in the round it reads it, and ends with an approval condition for each,
as `plan-work` requires. The decisions above settle the questions a lens would otherwise raise:
- why an integration base, and how it returns to `main` (Decision 1);
- port collisions and the fallback (Decision 2);
- the view's lifetime, who stops it, and a view from another generation (Decision 3);
- who records why the supervisor stopped, and a killed supervisor (Decision 4);
- an nvm Node that isn't on `PATH` (Decision 6);
- where each refusal happens relative to the first write or signal (Decisions 6 and 7);
- a Node the queue's checks run under that is below the minimum (Decision 9);
- older records and locks (Decisions 8 and 9).

A finding against one of them should name the decision it disputes.
