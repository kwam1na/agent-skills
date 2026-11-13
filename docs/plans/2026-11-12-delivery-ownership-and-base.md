---
reviewSlug: delivery-ownership-and-base
---

# Change ownership and the delivery base without voiding checks

Epic V26-3307. The seven native Linear children are the delivery scope. The epic description lists six and V26-3403 adds the lane-side review carry. Start from main at 3007ebc with the verified linear-v2.2.0 installation. Use a delivery-specific integration branch so the release installation and delivery bookkeeping do not move main while other queues are checking. Land the reviewed product and delivery evidence on main through the integration review and queue. The generation used to deliver this work is distinct from the future generation that contains it.

## Finish line

- Both plan lenses align. Each child meets its Linear criteria and lands through the queue, or closes with its specific reason recorded.
- The reviewed integration reaches main. Run the full local suite on macOS and Python 3.11 on the resulting head, and record Ubuntu and cut-release CI for that main push.
- A delivery run on the next approved release demonstrates that moving ownership needs no base push and that only a deliberate integration step needs a manual queue hold. A fixture proves both behaviors before merge. Keep the live-release verification open until the approved release has run.
- Record reviews, checks, merge evidence and the next-release verification separately.

## Out of scope

- A distributed lock spanning machines and work from the related queue and tracker epics.
- Force-moving remote refs, automatic conflict resolution for substantive source changes, and new forge or host integrations.
- A release tag outside the repository's batch release process.

## Units and order

The currently installed delivery generation still enforces whole-file ownership. One lane holds the shared workflow files for this delivery. Each story below edits the workflow entry point and its shared fixtures. This delivery also provides evidence of the serial work imposed by whole-file ownership.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3251, V26-3253, V26-3252, V26-3403, V26-3254, V26-3301, V26-3255 | `skills/run-delivery/`, `skills/orchestrate-work/`, `skills/delivery-views/`, `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`, `tests/test_lane_rows.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_merge_queue.py`, `tests/test_own_deliveries.py`, `tests/test_linear_qualification.py`, `provenance.lock.json`, `catalog.json`, `profiles/`, `docs/solutions/`, `docs/own-deliveries.md`, `tests/test_update_provenance.py` |

Review the outcome and adversarial cases independently. Run the lane's focused tests and installed run-delivery gates before hand-off. Select the affected catalogue tests in the merge queue along with its configured guards. Use the full suite for integration. Keep fixtures in separate temporary repositories and use the worktree-private Python environment. The normal delivery review bound is four rounds. Search corpus pins and schema fixtures when adding a command or record field. Update catalog, selecting profiles and provenance together for changed releasable skills.

## Stories

### V26-3251. Keep lane ownership and story lanes in the record

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `skills/delivery-views/`, `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`, `tests/test_lane_rows.py` and `tests/test_run_delivery_supervisor.py`.

Seed ownership and planned story lanes atomically during initialization. Use the existing state transaction and writer checks for a lead-only ownership transfer and atomic story reassignment. Read effective ownership and lane from the record at lane start, review, hand-off, and acceptance. Reassignment of a candidate already handed off must preserve its evidence and cannot silently change its authorization. A record without the new fields continues to use its original delivery config until upgraded explicitly. Export effective assignments to the archived delivery configuration at close.

Skip exclusivity and candidate ownership checks only for declared regenerable outputs whose existing regeneration and listing guards run in the queue. Prove two valid provenance refreshes are accepted. In a separate fixture, hold a sensor after it signals that its check is running. Transfer ownership through the lead command, then use the normal lane command to prove the old owner is denied and the new owner is allowed. Assert the remote base head and active check identity stayed the same, release the sensor, and require that original check to pass. Test duplicate owners, denied writers, stale config, and two concurrent lead mutations.

### V26-3253. Make lead changes that must touch the base wait for the merge slot

Lane 1, tier 2, after V26-3251. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py`, `tests/test_merge_queue.py` and `tests/test_orchestration_state.py`.

Add a lead push operation that takes the same queue lock as candidate processing, checks the base again inside that lock, and uses a normal fast-forward push. Preserve the existing distinction between committing a plan change and publishing it. Handle failed pushes and ensure the lock is released. Name the required hold shape when validation fails.

Write live flake declarations through the state transaction, scoped to the exact remote base head and check id. Read them at the retry decision, so a declaration made after a sensor starts can cover its failure. Preserve the existing failure-match and one-retry rules. A later binding declaration supersedes the temporary entry. Hold a sensor after its start signal, start the lead push, and require the push to land after the original check passes. In a separate run, hold a started sensor with no matching declaration, declare a flake through the lead command, then release the sensor to fail on precisely that test. Require one recorded retry at the same check identity and a passing retry. Also test an external base move, stale flake entries, and mixed failures.

### V26-3252. Let two lanes work in one large file at once

Lane 1, tier 3, after V26-3251 and V26-3253. Characterization-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py` and `tests/test_merge_queue.py`.

Choose explicit region ownership. Assignment and review preparation can detect overlapping claims before candidates reach the queue, including when neither branch has been pushed. Start with named Python functions and methods, identified by qualified name from the parsed source. Ordinary files keep whole-file ownership. A path can have region owners or a whole-file owner. File creation, deletion, rename, non-function edits, and changes whose ownership cannot be resolved require whole-file ownership. Include decorators and the function signature in each region. Region checks inspect the committed diff against the merge base, so shifts caused by earlier edits do not change ownership. Reject an edit that crosses a region boundary and reject a second assignment that claims an already-owned region. Use the same region interpretation for assignment warnings, review preparation and acceptance. A changed claim invalidates pending review authorization unless the candidate still passes the current ownership check.

Characterize whole-file behavior first. With both disjoint claims active, prove two real branches from the same base can each change a distinct function in one file, prepare review, hand off and pass normal lead acceptance into the queue. Merge the first and exercise queue sync for the second. With the same valid claims, have the second candidate edit the first lane’s function and prove review preparation refuses before writing a brief. Separately prove duplicate claims are refused. Exercise changed decorators, deletions, context-only changes, duplicate names and edits at region boundaries. Preserve queue sync and conflict checks as the final check on the actual merged tree.

### V26-3403. Let a lane keep its review across a lead-directed base merge

Lane 1, tier 4, after V26-3252. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py` and `tests/test_orchestration_state.py`.

Have the lead record the authorized story, reviewed head and allowed base commit or commits before the merge. Hand-off requires that authorization for every merged base commit. Let hand-off carry review evidence from the last aligned candidate when its first-parent history since review consists exclusively of two-parent merges of commits on the delivery base. Compare reviewed and current story deltas against their respective base ancestors. Also refuse when a base merge changes lane-owned content, reporting the file. Compare whole files for whole-file ownership and the owned regions for a region claim. Journal both heads and the merged base commits. Gate evidence must still cover the handed head. The queued carry keeps its existing rules. Prove a real authorized base merge with an unchanged story delta, unchanged owned content and passing gates hands off without another round, with both heads and the authorized commits recorded. Include a shared file with only an unowned function changed upstream. Pin that a lane commit, unauthorized base merge, unrelated merge, or manual conflict edit requires a new review. Update the lane brief.

### V26-3254. Make an integration base a supported delivery mode

Lane 1, tier 5, after V26-3403. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py`, `tests/test_own_deliveries.py` and `tests/test_run_delivery_supervisor.py`.

Use the binding's existing served-base patterns and delivery base identity. Let setup create or resume a named integration base with its initial binding configured and destination recorded. Resolve the lead's actual record location in lane briefs. Extend the standing-binding test with a declared integration mode and continue checking the main binding. Prove setup through supervisor close-out and full-suite completion on an integration fixture. For final integration, use a reviewed recipe that merges the current default branch, restores its binding, and reruns integration review and gates. Retain the product source, tests, catalog, profiles, provenance, final plan, plan review record, delivery configuration, brief and any lane notes. Restore main’s standing binding and delivery posture. Keep the integration base’s v2.2.0 adoption, host projections and branch-specific binding changes out of the main tree. Before final review, compare those paths with current main and confirm its installed archive digest and binding match. Keep local records, temporary fixtures and operational logs in the delivery archive.

### V26-3301. Switch the lead generation before a lane writes the record

Lane 1, tier 6, after V26-3254. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py`, `tests/test_orchestration_state.py` and `docs/solutions/`.

Pin the writer generation by the applied archive digest. An installed tool with a different writer identity refuses record writes and reports both generations. Check the identity under the state transaction lock before any mutation. Keep unpinned legacy records readable and require a lead transition to pin them. The lead upgrades the writer while the supervisor and queue are parked, checks the new reader can load the record, and resumes from that generation. The integration-base move uses that transition when it installs a new generation. Prove a rejected write leaves record and journal bytes unchanged and the same write succeeds after lead upgrade. Document the old-reader schema error, its cause, and the recovery.

### V26-3255. Move a long-lived delivery base onto a newer default branch

Lane 1, tier 7, after V26-3254 and V26-3301. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `tests/test_run_delivery.py`, `tests/test_update_provenance.py`, `tests/test_orchestration_state.py`, `docs/own-deliveries.md` and `docs/solutions/`.

Merge the newer default-branch history into the integration base. Preserving ancestry keeps every existing merged SHA reachable and avoids rewriting review evidence. Park the supervisor and use the queue lock while moving the base. Apply the default branch's verified generation using its supported lifecycle, regenerate provenance with the writer, and stop on substantive conflicts with paths and a resume instruction. Keep the old base authoritative until the new head is published and the record update is reconciled. On retry, detect a previously published head and complete record reconciliation once. Prove the one-command lock-only fixture, conflict and resume, and reachability of merged entries. Use a different archive digest in the recovery fixture and stop after the remote accepts the new head but before the record update. Verify the supervisor cannot process candidates against the stale record, then rerun and prove that the writer transition and base reconciliation each happen once, without a second commit or duplicate record entry. Check both generations’ writer admission and the reachability of the original merged SHAs. Tell lanes to start from the moved base and document how to record a shared flake fix pending upstream.
