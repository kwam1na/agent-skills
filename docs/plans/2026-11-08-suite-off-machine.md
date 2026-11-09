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
- The full local suite passes on macOS with Python 3.11 at that commit, and leaves no process running. The lead takes a process-table
  snapshot before the run and another after it. The after snapshot holds no new `run_delivery.py`, `render_state.py`, `merge_queue.py` or
  fixture-server process whose command line names an `FX-` fixture key or a path under the run's temporary folder. The delivery's own
  supervisor, and any other real delivery's processes, are left out by that rule, never case by case.
- No finding is deferred (operator ruling, 2026-11-08): every finding any review in this delivery raises, at any severity, is fixed in
  the story that found it, and no follow-up item is filed for it. The epic and its items are closed with this evidence.

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
   that leaks still stops what it started, by recorded pid. Both are required: the fixture's cleanup is never the only guard. Because the
   exit guard would reap a leak on its own once a case's folder is removed, each guard is proved with the other out of the way (V26-3273).

## Units and order

Two lanes. Lane 1 holds the run-delivery chain. Lane 2 holds the tracker module and the standing-binding tests. No file is shared.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3286, V26-3273, V26-3325, V26-3324, V26-3330 | `skills/run-delivery/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_plan_review.py`, `docs/solutions/`, `provenance.lock.json` for `run_delivery.py`'s digest, and `catalog.json` and `profiles/*.json` only if an output is added |
| 1 | 2 | V26-3274, V26-3292, V26-3312, V26-3316, V26-3328 | `tests/test_run_delivery_tracker.py`, `tests/test_own_deliveries.py`, `tests/test_delivery_views_render.py`, `tests/test_validate.py` |

**Moving ownership.** `provenance.lock.json` belongs to lane 1, the only lane that changes a listed output. If lane 2 finds it needs a file
outside its `owns`, it raises it with the lead before hand-off.

**Shared resources.** V26-3273's rows start real supervisors. Each records its pids and stops them in cleanup, and a module that can't share
the machine is marked `run_serially`. No lane runs a `lead-*` command from the main checkout, and no test reaches `.orchestration/` or
`.agents/deliveries/` outside its own folder.

## Stories

### V26-3286. A test_run_delivery case runs a lead command against the real checkout's live delivery

Lane 1, tier 1, not blocked. Characterization-first: reproduce the stray `supervisor:` lines and name the case or cases before changing
anything. Then every `run_delivery.py` invocation in `tests/test_run_delivery.py` and its siblings runs with an explicit fixture repository
and state directory, and the module's cleaned environment strips every variable `Config` or `setting()` reads, `DELIVERY_STATE` included.
The guard row builds its own main checkout with a linked worktree, and plants a delivery under that main checkout's `.orchestration/` and
`.agents/deliveries/`. It sets `DELIVERY_STATE` and every other variable `Config` or `setting()` reads, current and legacy names alike, to
point at the planted delivery in the test process's own environment (`os.environ`, through `mock.patch.dict`) before the `Fixture` and
its cleaned environment are built. It then runs the module's lead commands from the worktree with the module's ordinary cleaned
environment, never with those variables passed in `env=`. It detects contact by comparing the planted files byte for byte, content and
mtime, before and after, and by a planted marker the tool would print or copy; never by atime. The lane records two red runs of the guard: one on
the unfixed tree that reproduces the named case, and one with the environment strip reverted. Then it records the green run after the fix. The hand-off records whether
any case ever wrote to a live record, and names the write if one did. Files: `tests/test_run_delivery.py`, its siblings in lane 1, and
`run_delivery.py` only if the repository is resolved from the working directory where it shouldn't be. Sensors: `tests/test_run_delivery.py`,
`scripts/measure-test-writes.py`.

### V26-3273. A test run leaves fixture lead-run supervisors spinning after it ends

Lane 1, tier 1, after V26-3286. Characterization-first: find the test or tests that can start an `FX-1` supervisor and outlive it, before
changing anything. Each stops what it started by recorded pid, even when it fails or is interrupted. The outlives row looks while the
case's folder still exists, with the exit guard out of the way (the state directory and install kept), and finds supervisors independently
of the test's own records: a process-table scan for `run_delivery.py`, `render_state.py` or `merge_queue.py` processes whose command line
names the case's temporary folder, or runs from that folder, or names the case's install with an `--delivery FX-` key, so a
supervisor whose children have died is still found. The lane records it red against the unfixed leaking case. `Supervisor.run` exits when its state
directory or its install disappears (Decision 3), proved with the test's own cleanup disabled, by three rows. One removes the state
directory under a running supervisor and asserts it exits on its own within a bound. One removes the install a running supervisor was
started from, a per-case install copy rather than the module-shared `installed_release()`, keeping its state directory, and asserts the
same. The control keeps both, and asserts the supervisor is still alive after more than one pass. The hand-off records whether a leaked supervisor slows a later test in the
same run. Files: `run_delivery.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, and `provenance.lock.json` for the
digest. Sensors: both test modules, a process-table check after the suite, `scripts/validate-registry.py`.
Compounding: the solution note the item names.

### V26-3274. Serve the remaining test HTTP servers without the 34 s name lookup on macOS

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 2. The row constructs `FakeJira()` itself, the class every case in the
module uses, in the test's own process, with `socket.getfqdn` patched to sleep 60 s for the loopback names V26-3232's `STALLED_LOOKUP`
lists. It asserts that the constructor returns and the socket accepts a connection within 5 s. A copy of the server run in a subprocess
doesn't count. The lane records the red run against the stock `ThreadingHTTPServer`. The hand-off
carries the search of `tests/` Decision 2 asks for. Before and after timings for `tests.test_run_delivery_tracker` come from macOS CI with
the `full-matrix` label. Files: `tests/test_run_delivery_tracker.py`. Sensors: `tests/test_run_delivery_tracker.py`, and macOS CI with
`full-matrix`.

### V26-3292. Pin this repository's regenerable.listings entry in its standing-binding tests

Lane 2, tier 1, after V26-3274. Test-first, per the item. A `StandingBinding` row in `tests/test_own_deliveries.py` asserts the exact entry
for `provenance.lock.json`, its path and argv. It runs that argv the way `merge_queue.listed_outputs` does: the committed lock's bytes
(`git show HEAD:provenance.lock.json`) on stdin, cwd at the root, and no file argument. It asserts the output equals an expected array
computed independently from the lock JSON's listed output paths, sorted, never through `update-provenance.py`'s own code. The row is proved
red three ways: the entry removed, an argv that prints `[]`, and an argv naming another script. For the last two, the exact-entry literal is
updated to match each mutated argv, so the failure lands on the run assertion, and each red run records which assertion failed. Files: `tests/test_own_deliveries.py`. Sensors: `tests/test_own_deliveries.py`, `scripts/validate-registry.py`.

### V26-3312. Gate the run-deadline render test against the macOS wall-clock race

Lane 2, after V26-3292 (lead's addition, 2026-11-08, under the no-deferrals ruling: found red on V26-3274's macOS full-matrix job,
outside that story's diff). Characterization-first: reproduce `CollectRunsTests.test_runs_past_the_deadline_are_unavailable_and_stay_uncached`
under load, or with a stall that models it, before changing it. The fix keeps the row's claim and doesn't loosen it. Either the suffix order
is a promise `collect_runs` keeps under load and a row pins it, or the row asserts what the code does promise, and the hand-off says which.
A mutation that drops the deadline handling fails it. Files: `tests/test_delivery_views_render.py`, and
`skills/delivery-views/scripts/render.py` only through the lead, if the code breaks a promise. Sensors:
`tests/test_delivery_views_render.py`, macOS CI with `full-matrix`.

### V26-3316. The adoption-guide test's pinned focus patch races the fixture supervisor

Lane 2, after V26-3312 (lead's addition, 2026-11-09, under the no-deferrals ruling). Found red on V26-3312's Ubuntu job, outside that
story's diff: `RunDeliveryAdoptionGuideTests.test_the_guide_takes_an_empty_repository_to_a_closed_delivery` got `version_stale` on the
guide's pinned `focus ... --state done`. Characterization-first, read-only: name the writer that bumps the record between the read and
the pinned write, and reproduce the failure with a stall that models it, before changing anything. The fix follows the cause. If an
adopter's guide sequence races, the guide changes, and the tool too if needed. If only the test's fixture races, the test serializes
against its own writer without weakening what it proves. The hand-off says which. Lead's ruling on the diagnosis (2026-11-09): the merge queue
writes the record inside `set_focus`'s read-then-pinned-write window, so the fix is a bounded re-read-and-retry in `set_focus`, like
reconcile's, with a row in `tests/test_orchestration_state.py` that fails with the retry reverted. The guide's text stays. Lane 2 owns
`delivery_state.py` and `tests/test_orchestration_state.py` for it, and `provenance.lock.json` moves to lane 2 before its acceptance. The
second defect the diagnosis found, `stop_supervisors`' `rglob` walk raising mid-walk and leaking a supervisor, is V26-3273's. Files: decided by the diagnosis. Those under lane 1's
`owns` (`tests/test_run_delivery.py`, `skills/run-delivery/`) move to lane 2 only through the lead, once lane 1's stories on them have
merged. Sensors: `RunDeliveryAdoptionGuideTests`, the adoption-guide corpus pins.

### V26-3324. In-process main() tests print a live delivery's supervisor status and take its lock

Lane 1, after V26-3273 merges (lead's addition, 2026-11-09, under the no-deferrals ruling). V26-3267's lead reported, and lane 1
characterized, that in-process `main()` tests in `tests/test_run_delivery.py` reach a live delivery through `supervisor_line(cfg())`, whose
`cfg()` resolves the repository from the test process's working directory and caches it in the module-global `CFG`. `read_lock` can also
briefly take a live supervisor's lock. The fix keeps every in-process call inside its case, and a guard row run from a live-looking
checkout with a planted delivery and held lock proves it, red before and green after. It also names and fixes the run's un-waited Popen
(the ResourceWarning), and says whether any product path can reach a delivery outside its repository. Files: lane 1's. Sensors: both
run-delivery test modules, under `-W error::ResourceWarning` from a planted live checkout.

### V26-3325. Remove the ticket ids from the detached-supervisors solution note

Lane 1, first, ahead of V26-3324 (lead's addition, 2026-11-09: `main` broke at V26-3273's merge, because its note names ticket ids and
the lead's narrow selection skipped the corpus pin). Sensor-only: reword the note's id lines, keeping the lessons. Its queue selection is
`tests.test_linear_qualification`, plus validate-registry and the provenance check. Files: that note, and `provenance.lock.json` if it lists
the note. Sensors: `tests.test_linear_qualification`, `scripts/validate-registry.py`.

### V26-3328. The corpus read-only test hashes the operator's live delivery records

Lane 2 (lead's addition, 2026-11-09, under the no-deferrals ruling: the close-out's full suite from the operator's checkout at `4f9c965`
failed only `test_valid_corpus_is_stable_and_read_only`, which digests every file under the checkout, git-ignored `.orchestration/` live
records included; it passes in a clean worktree). Test-first: a row green while an ignored `.orchestration/` file changes during validation
and red when validation writes a tracked or corpus file; then the digest leaves ignored paths out. A search of `tests/` for other
whole-checkout scans of ignored live state, each fixed or recorded as none. Files: `tests/test_validate.py`, and any other test file the
search names through the lead. Sensors: `tests/test_validate.py` from a checkout with a live record changing, `tests.test_linear_qualification`.

### V26-3330. adapter-test and app-check accept another run's server on a shared port

Lane 1 (lead's addition, 2026-11-09, under the no-deferrals ruling: the adoption-guide case's `Connection refused` in two deliveries'
`fullHealth`, which lane 1 traced to concurrent suites sharing the guide binding's fixed `inApp.port` 47613). `adapter_contract` and
`run_in_app` accept any listener after their ready-wait, so a serve that lost the bind dies silently and the run tests another run's server.
Both refuse unless their own serve still runs, naming its exit and stderr tail; the guide case takes a free port through a new pinned
substitution; a two-run race row is red before and green after for both. Files: lane 1's (`run_delivery.py`, `references/adopting.md`,
`tests/test_run_delivery.py`, the lock). Sensors: `RunDeliveryAdoptionGuideTests`, the in-app rows, the two-run race,
`tests.test_linear_qualification`.

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
