# Amendment 3: publishing reuses CI's result for the tagged commit

The operator objected to an hour-plus wait from tag to published. This overrides the plan's tag-driven publish unit, its
Channel scenario and its Permissions decision where they differ.

- **No matrix at the tag.** The publish job requires that CI already passed on the exact tagged commit: the `CI` run for that
  commit on a push to the default branch, with every matrix job and the `cut-release` job concluded `success`. It waits for a
  run still in progress, bounded at 150 minutes. A failed, cancelled or missing run publishes nothing. `ci.yml` no longer needs
  `workflow_call`, and the job calling it goes.
- **Publishing still requires the full matrix to pass.** Only where the proof comes from changes.
- **Permissions.** The job reading the run holds `actions: read`. Write permissions stay on the publish job alone.
- **Scenarios.** `tests/test_release_channel.py` runs the decision for real across success, failure, cancelled, missing and in
  progress, and for a run on the wrong commit, on the wrong event and on the wrong branch.
- **Workers.** CI passes an explicit `--workers` on macOS, sized to the runner, because the suite's default ran one unit at a
  time there. Jobs set `timeout-minutes` at 120.
