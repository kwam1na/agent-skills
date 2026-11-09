## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `main`, as the delivery's grant states. No lane
merges, marks a pull request ready, tags, or applies a release.

**No deferrals (operator ruling, 2026-11-08).** Nothing found in this delivery is deferred. Every finding is fixed in the
story that found it, before hand-off, whatever its severity, cost or round. This overrides the brief's Triage and Hand-off
sections where they say otherwise:
- A P2 or P3 a lens files as deferred is fixed in the lane, the same as a blocking finding.
- A late P1 listed in `lateFindings` is fixed too.
- A note a lens leaves in its evidence (for example "P2, not filed") is a finding, and is fixed.
- A finding first raised in a later round is fixed, not judged on scope.
- Nothing goes to the lead as an expensive non-blocking finding: `handoff` carries no `--findings`, and no follow-up item is
  filed for anything this delivery found.

If a fix needs a file outside your `owns`, ask the lead to move it to you. Don't leave the finding open. If a fix is out of
reach (it needs an operator decision, or another epic's change), stop and tell the lead before hand-off. Don't hand off
around it.

**Ownership.** Lane 1 holds the run-delivery chain and `provenance.lock.json`. Lane 2 holds `tests/test_run_delivery_tracker.py` and
`tests/test_own_deliveries.py`. A lane that finds it needs a file outside its `owns` raises it with the lead before hand-off.

**Lane 1: V26-3286 first.** Reproduce the stray `supervisor:` lines and name the case before you change it. Never run a `lead-*`
command, or a test module, from the main checkout: only from your story's own checkout.

**Lane 2: V26-3274.** Record the stalled-lookup row's red run. Ask the lead to put the `full-matrix` label on your pull request so the
macOS job runs on it, and include that job's timings for `tests.test_run_delivery_tracker` in your hand-off, beside the last `main` run's.

**Shell.** Never set `TMPDIR` under a checkout. Stop only processes you started, by the pid you recorded, never by pattern with `pkill`
or `killall`. Clear `skills/` bytecode before anything that builds a release.
