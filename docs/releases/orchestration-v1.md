# orchestration-v1

## Version and set names

Nothing reads the corpus `pyproject.toml` version (0.1.0), and earlier releases never changed it. A release is identified by its release ids and qualification set names, so the bump is a new themed set for every profile:

| Profile | Release id | Checked product set |
| --- | --- | --- |
| core | `core-workflows-orchestration-v1` | `qualifications/product/core-orchestration-v1.*` |
| linear | `linear-workflows-orchestration-v1` | `qualifications/product/linear-orchestration-v1.*` |
| provider | qualified against the core archive | `qualifications/product/provider-orchestration-v1.*` |
| bootstrap | `bootstrap-workflows-orchestration-v1` | `qualifications/product/bootstrap-orchestration-v1.*` |

Retained records keep their bytes, including the concurrent-delivery set. The current-set pointers in the qualification tests and the README move to these names.

`pyproject.toml` stays at 0.1.0, because nothing reads it. This was the lead's decision.

## Release notes

### New: orchestrate-work

An epic can be delivered by a lead and several lanes, without the operator in the inner loop.

**Delivery state**
- A delivery-state record and an append-only journal, with byte-exact input and output on every platform.
- Lanes write their own hand-offs, review rounds and focus. The lead keeps authority over the queue, the plan and the headline.
- The status line is derived from the record unless the lead overrides it. An override expires.
- Every write adds to a bounded change log.
- `next --lane` ranks the work a lane could take next, with a reason for each item, and assigns nothing. It weighs:
  - tracker blockers;
  - file collisions with open lanes, using the files a change actually touched where known;
  - priority;
  - the lane's own files.

**Views**
- Built-in views: a markdown view and a static page.
- A render wrapper with bounded output. An interrupt at any point ends it non-zero, and it stops the commands it started.
- Publishers for repositories with a publish command, and an outbox for views a host publishes itself.
- A live view re-renders on every write and pushes the record to open pages.
- The views show each ticket's tracker title and summary, review rounds, per-stop delivery stats and queue estimates.

**Reconciliation**
- The record is reconciled from forge and tracker snapshots. A merge the runner never recorded can be recovered, and an interrupted recovery is finished.
- A neutral `list-work` tracker operation.
- A binding can declare forge, tracker and work-hints snapshot commands, which `serve` refreshes on an interval.
- Every snapshot command runs bounded, and whatever it leaves running is stopped. Nothing outside its run is touched.
- Dependency lists are checked the same way for list-work results and tracker snapshots. Self-blocks, duplicates, cycles and parent links are refused.

**Merge-queue runner**
- Driven by a repository binding and an affected-checks provider.
- Batch mode: one check run for several candidates, landing that is verified against the tree, parallel checks, and a culprit search when a batch fails.
- Check visibility: live running checks, timing against history, failing tests and logs, and declared flakes retried once.
- `serve` lands the queue continuously.
- The workRoot is checked against git's own view, and the runner's own git is isolated from global and system configuration, pathspec settings and junctions.
- A killed run's leftovers are cleared at the next start.

**Routing and qualification**
- The `orchestrate` workflow kind, routed from `deliver-work`.
- Core and provider qualification now include an orchestration scenario. It runs the shipped state tool, the render wrapper and the merge-queue runner from the released archive.

### Product and tooling

- The product launcher stops descendants that leave its process group. On macOS it identifies processes by the kernel's unique id, and it never selects a process outside its run.
- A provenance lock writer, so rebases regenerate digests.
- A parallel test runner (`scripts/run-tests.py`). It gives the same verdict as `unittest discover`, and records per-test timings. With `--unit-timeout`, a hung unit is stopped with its process group and reported as timed out, naming its tests. The default limit comes from earlier timings, or is 1800 s.
- Hosted CI runs the suite through `scripts/run-tests.py` instead of `unittest discover`, so it matches the merge checks when it is turned back on.
- A fixture adopter runs the orchestration workflow end to end.

### Compatibility

- The new skill is additive. Existing workflows route as before, and `orchestrate` is chosen only for epic delivery.
- The binding gains optional `state` members: `trackerSnapshot`, `forgeSnapshot`, `workHints`, `refreshSeconds`, `snapshotMaxAgeSeconds` and `statusOverrideMaxAgeSeconds`.
- The new `work-snapshot/1` and `work-hints/1` documents are additive.
