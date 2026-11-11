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
  - the operator merges it;
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
     reviewed with the delivery's lenses, and the operator merges it.
   - If V26-3406 is still open when both stories merge here, the delivery waits on that finish line, and the record shows it.
2. **The live view's port is derived, then recorded.**
   - The port is derived from the repository's top-level path and the delivery key: the sha256 of `<path>\0<key>`, reduced into 20000–29999.
     The same delivery in the same checkout always asks for the same port.
   - The supervisor records the port it bound in `live.json` in the delivery's state directory: `port`, the view's `pid`, and when it
     started. A restart asks for the recorded port before the derived one.
   - When that port is held by a process that isn't this delivery's view, the supervisor binds a free port instead and records it. It
     says so on its own output and in the next status line, naming the port it wanted and who holds it (`port_holders`).
   - `RUN_DELIVERY_LIVE_PORT=0` makes the view bind a free port, for tests and for a lead who wants that.
3. **The live view outlives the supervisor.**
   - The supervisor starts the view in a session of its own, not in its process group. A stopped or killed supervisor leaves the view
     serving.
   - A starting supervisor first looks for a view of this delivery already serving. That is `live.json`'s pid, alive and listening on
     its port. It adopts that view rather than starting another.
   - The view stays read-only: it serves the record and changes nothing.
   - `lead-close` stops the view last, after the archive lands, by its recorded pid. `lead-init --replace` stops it with the supervisor
     it replaces. Nothing stops it by pattern.
4. **The view says why the supervisor is down.**
   - The supervisor writes `supervisor.json` in the state directory: its pid, `running` or `stopped`, when, and why. The reasons are:
     - `close`: the close-out stopped it;
     - `replaced`: `lead-init --replace`;
     - `signal`: a deliberate stop by signal, naming it;
     - `held`: it stopped after a pass on the lead's request.
   - `render_state.py live` gains `--status-file PATH`, generic to orchestrate-work. It re-reads the file on each render.
   - While the file says `stopped`, or names a pid that no longer runs, the view shows a banner: the supervisor is down, since when, and
     why. A dead pid with a `running` mark shows as "stopped without recording why", which is what a killed supervisor leaves. The banner
     names `lead-run --resume`.
   - Without `--status-file` the view renders as today.
5. **Every lead command's status line names the address, also when the supervisor is down.** The down line reads
   `supervisor: down (<why>); live view <address>; restart it with ... lead-run --resume`. It reads the address from `live.json`
   when the view still serves, and omits it when not.
6. **"The Node a process uses" is the first `node` on that process's `PATH`.** Children resolve `node` through `PATH`, so an nvm Node
   further down doesn't count.
   - `doctor`'s Node row checks the Node first on `PATH`. When that Node is too old and an nvm Node meets the minimum, the row is `wrong`
     and names that nvm Node's `bin` folder as the entry to put first. `load_harness` keeps its own nvm lookup, since it runs Node itself.
   - `lead-run` refuses to start when the Node first on its `PATH` is below the runtime's `nodeMinimum`, or when there is none. It
     refuses before it takes the lock or starts a child, naming the `PATH` entry, the version and the minimum.
   - `lead-init`, and every command that starts a supervisor detached, runs the same check before it writes anything, so the refusal
     reaches the lead's shell rather than a log.
   - A generation without a runtime asks for no Node, as today.
7. **`lane-start` checks the lane's Node and Python.**
   - Node: as Decision 6, against the runtime's `nodeMinimum`.
   - Python: the interpreter running the tool and the first `python3` on `PATH` must each be 3.11 or newer, the minimum `doctor`
     already holds.
   - It refuses before it creates the checkout or records a start, naming what it found and the fix.
8. **The supervisor records its Node, and `doctor` reports it.**
   - At start, the supervisor writes the Node first on its `PATH`, path and version, into its lock record.
   - `doctor --delivery`'s supervisor row reports it beside the pid and address.
   - The row is `wrong` when that Node is below the minimum. A supervisor started by an older generation may have no Node recorded; the
     row then says so, as `warn`.
9. **The queue records each check's Node.**
   - Each check result entry in the record gains an optional `node` member: the version of the first `node` on the `PATH` the check
     ran with, after its profile's environment. It is `null` when there was none.
   - orchestrate-work's state schema gains the member as optional, so existing records still validate.
   - The live view shows it in the check's detail.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3260, V26-3262 | `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_merge_queue.py`, `tests/test_orchestration_render.py`, `tests/test_orchestration_state.py`, `catalog.json`, `profiles/`, `provenance.lock.json` and `docs/solutions/` |

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

1. With a Node below the runtime's `nodeMinimum` first on `PATH`, `lead-run` exits non-zero before taking the lock or starting a child,
   and its message names the `PATH` entry, the version found and the minimum.
2. With no `node` on `PATH`, `lead-run` refuses the same way, saying none was found.
3. With a Node below the minimum first on `PATH`, `lead-init` refuses before it writes the record.
4. `lane-start` refuses, before creating the checkout or recording a start:
   - with a Node below the minimum first on `PATH`, naming the version, the minimum and the fix;
   - with `python3` below 3.11 first on `PATH`, naming the version, the minimum and the fix.
5. `doctor`'s Node row is `wrong` when the Node first on `PATH` is below the minimum, even with an nvm Node that meets it. The row then
   names that nvm Node's `bin` folder as the entry to put first.
6. A supervisor started with a qualifying Node records that Node's path and version in its lock. `doctor --delivery`'s supervisor row
   reports them.
7. The supervisor row is `wrong` when the recorded Node is below the minimum, and `warn` when the lock records none.
8. A queue check result carries `node`: the version first on the `PATH` it ran with, or `null` when there was none.
9. A record without `node` on its check results still validates.
10. A generation whose runtime declares no `nodeMinimum` makes no Node refusal in `lead-run`, `lead-init` or `lane-start`.

### V26-3262. Give the live view one address that survives supervisor restarts

Lane 1, tier 1, after V26-3260. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/references/adopting.md`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/test_run_delivery_supervisor.py`, `tests/test_orchestration_render.py`, `catalog.json`, `profiles/` and
`provenance.lock.json`.

Acceptance criteria:

1. Stopping the supervisor and resuming it with `lead-run --resume` keeps the same live view address, and the view is the same process.
2. With the supervisor and its view both stopped, a restart binds the port recorded in `live.json`.
3. With that port held by another process, the restart binds a free port, records it, and says on its output and in the next status line
   which port it wanted and which process holds it.
4. With the supervisor stopped, the view still answers. Its page shows a banner saying the supervisor is down, since when, and why:
   - the close-out;
   - `lead-init --replace`;
   - a named signal;
   - a held stop.
5. With the supervisor killed by `SIGKILL`, the page's banner says it stopped without recording why, and names `lead-run --resume`.
6. Every lead command's status line names the live view's address while the view serves, whether the supervisor is running or down.
7. `lead-close` stops the view last, by its recorded pid, and leaves no process listening on its port.
8. `lead-init --replace` stops the replaced delivery's view.
9. `render_state.py live` without `--status-file` renders as it did before.
10. Two deliveries in the same checkout derive different ports.

## Shared resources

- **Ports.** The supervisor tests start real views. They set `RUN_DELIVERY_LIVE_PORT=0` unless the test is about the derived or recorded
  port. A test that binds a derived or recorded port, or holds a port to force the fallback, marks its class `run_serially = True`.
- **Processes.** Tests stop only the pids they recorded (AGENTS.md). The view now runs in its own session, so every test that starts a
  supervisor stops its view by `live.json`'s pid in teardown.
- **Node on `PATH`.** Tests that need a too-old or missing Node build a `PATH` in their own temporary folder with a stub `node` and
  `python3` that print a version. They never depend on the machine's nvm. The launcher rows that need a real Node 22 keep needing it
  first on `PATH` (the lead's machine has Node 20.19 as nvm's default).
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
- The pull request from `sync/v26-3306` into `main`, and its merge, which is the operator's.

## Plan review

Every lens raises every finding it can state against this plan in the round it reads it, and ends with an approval condition for each,
as `plan-work` requires. The decisions above settle the questions a lens would otherwise raise:
- why an integration base, and how it returns to `main` (Decision 1);
- port collisions and the fallback (Decision 2);
- the view's lifetime and who stops it (Decision 3);
- a killed supervisor (Decision 4);
- an nvm Node that isn't on `PATH` (Decision 6);
- where each refusal happens relative to the first write (Decisions 6 and 7);
- older records and locks (Decisions 8 and 9).

A finding against one of them should name the decision it disputes.
