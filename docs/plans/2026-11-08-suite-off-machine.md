---
reviewSlug: suite-off-machine
---

# Keep the test suite off the operator's machine

Epic V26-3304: four backlog items where this repository's own suite reaches outside its temporary folders. It leaves fixture supervisors
spinning, runs a lead command against the real checkout's live delivery, makes macOS wait on a name lookup, and leaves this repository's
`regenerable.listings` entry unpinned. Each item's tracker scope binds what it does. This plan decides only what the items can't: order, file
ownership, and how the work reaches `main`.

## Finish line

- Each item is merged to `main` through the merge queue, or closed with a recorded reason.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- The full local suite passes on macOS with Python 3.11 at that commit, and leaves no process running: a process-table check after the run
  finds no `run_delivery.py`, `render_state.py` or fixture server whose parent is gone.
- Every deferral has an open follow-up item filed under the epic, and the epic and its items are closed with this evidence.

## Out of scope

- The other backlog epics: lead hand-work (V26-3250), release safety (V26-3266), posture readers (V26-3267), and the harness runtime (V26-3268).
- Cutting or applying a release. Nothing here changes a contract an adopter sees, and `run_delivery.py`'s exit on a lost state directory
  ships with the next batch release.
- Servers the suite doesn't start itself. `render_state.py`'s `_LiveServer` and the run-delivery fixtures' in-app serve were fixed under
  V26-3232.

## Decisions

1. **V26-3286 goes before V26-3273.** A case that reaches the real checkout may be where fixture supervisors leak from, so naming it first
   narrows V26-3273's search. Both change `tests/test_run_delivery.py`, so they share a lane.
2. **V26-3274 uses the same override, not a shared helper.** The fake Jira server in `tests/test_run_delivery_tracker.py` names its
   address without a lookup, the way `STATIC_SERVE` in `tests/test_run_delivery.py` does. A helper both use would put lane 1's test file
   in lane 2's change. The item's criterion 1 allows either. Its other bullet, the delivery-views live tests' servers, was checked on
   2026-11-08 against `804df84`: no `tests/test_delivery_views*.py` module or page test starts a Python HTTP server. The live view's server
   is `render_state.py`'s `_LiveServer`, fixed and pinned by `tests/test_orchestration_render.py`, whose deliberately stock server there is
   the row's control and stays as it is. So the fake Jira server is the only one left. The lane confirms this with a search of `tests/` for
   every `HTTPServer` and `http.server` start, recorded in its hand-off, and if the search finds another server the lane raises it with the
   lead rather than leaving it.
3. **V26-3273's exit is a guard, not the fix for the leak.** A supervisor exits when its state directory or its install is gone. The test
   that leaks still stops what it started, by recorded pid. Both are required: the fixture's cleanup is never the only guard.

## Units and order

Two lanes. Lane 1 holds the run-delivery chain. Lane 2 holds the tracker module and the standing-binding tests. No file is shared.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3286, V26-3273 | `skills/run-delivery/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_plan_review.py`, `docs/solutions/`, `catalog.json` and `profiles/*.json` for `run_delivery.py`'s digest |
| 1 | 2 | V26-3274, V26-3292 | `tests/test_run_delivery_tracker.py`, `tests/test_own_deliveries.py` |

**Moving ownership.** `provenance.lock.json` belongs to lane 1, the only lane that changes a listed output. If lane 2 finds it needs a file
outside its `owns`, it raises it with the lead before hand-off.

**Shared resources.** V26-3273's rows start real supervisors. Each records its pids and stops them in cleanup, and a module that can't share
the machine is marked `run_serially`. No lane runs a `lead-*` command from the main checkout, and no test reaches `.orchestration/` or
`.agents/deliveries/` outside its own folder.

## Stories

### V26-3286. A test_run_delivery case runs a lead command against the real checkout's live delivery

Lane 1, tier 1, not blocked. Characterization-first: reproduce the stray `supervisor:` lines and name the case or cases before changing
anything. Then every `run_delivery.py` invocation in `tests/test_run_delivery.py` and its siblings runs with an explicit fixture repository
and state directory. The guard row runs the module's lead commands with the real checkout's delivery files present but out of reach, and
fails if any command reads `.agents/deliveries/` or `.orchestration/` outside the case's temporary folder. The hand-off records whether
any case ever wrote to a live record, and names the write if one did. Files: `tests/test_run_delivery.py`, its siblings in lane 1, and
`run_delivery.py` only if the repository is resolved from the working directory where it shouldn't be. Sensors: `tests/test_run_delivery.py`,
`scripts/measure-test-writes.py`.

### V26-3273. A test run leaves fixture lead-run supervisors spinning after it ends

Lane 1, tier 1, after V26-3286. Characterization-first: find the test or tests that can start an `FX-1` supervisor and outlive it, before
changing anything. Each stops what it started by recorded pid, even when it fails or is interrupted, and a row proves no supervisor outlives
its test. `Supervisor.run` exits when its state directory or its install disappears (Decision 3), and a row removes the state directory
under a running supervisor and asserts it exits within a bound. The hand-off records whether a leaked supervisor slows a later test in the
same run. Files: `run_delivery.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `catalog.json`, the profiles and
`provenance.lock.json` for the digest. Sensors: both test modules, a process-table check after the suite, `scripts/validate-registry.py`.
Compounding: the solution note the item names.

### V26-3274. Serve the remaining test HTTP servers without the 34 s name lookup on macOS

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 2. A row in each affected module runs its server under V26-3232's 60 s
stalled lookup and requires it to listen within 5 s, and the row fails without the fix; the lane records that red run. The hand-off
carries the search of `tests/` Decision 2 asks for. Before and after timings for `tests.test_run_delivery_tracker` come from macOS CI with
the `full-matrix` label. Files: `tests/test_run_delivery_tracker.py`. Sensors: `tests/test_run_delivery_tracker.py`, and macOS CI with
`full-matrix`.

### V26-3292. Pin this repository's regenerable.listings entry in its standing-binding tests

Lane 2, tier 1, after V26-3274. Test-first, per the item. A `StandingBinding` row in `tests/test_own_deliveries.py` asserts the exact entry
for `provenance.lock.json`, its path and argv, runs that argv on the committed lock, and asserts it prints exactly the lock's outputs in the
listing format the queue reads. The row is proved red three ways: the entry removed, an argv that prints `[]`, and an argv naming another
script. Files: `tests/test_own_deliveries.py`. Sensors: `tests/test_own_deliveries.py`, `scripts/validate-registry.py`.

## Execution posture

`characterization-first` for V26-3286 and V26-3273, `test-first` for V26-3274 and V26-3292. Lanes work to the focused lane profile: no full
suite in a lane. The queue runs the selected checks on the synced head, and the lead runs the full suite and the process-table check at the
close.

## Approval handoffs

- The plan itself, before `lead-setup`.
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its four items are in Linear. The lead posts at each milestone with the host's Linear tools and moves items to their final
state.
