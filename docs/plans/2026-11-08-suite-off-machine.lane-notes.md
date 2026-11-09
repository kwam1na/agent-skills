## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `main`, as the delivery's grant states. No lane
merges, marks a pull request ready, tags, or applies a release.

**Ownership.** Lane 1 holds the run-delivery chain and `provenance.lock.json`. Lane 2 holds `tests/test_run_delivery_tracker.py` and
`tests/test_own_deliveries.py`. A lane that finds it needs a file outside its `owns` raises it with the lead before hand-off.

**Lane 1: V26-3286 first.** Reproduce the stray `supervisor:` lines and name the case before you change it. Never run a `lead-*`
command, or a test module, from the main checkout: only from your story's own checkout.

**Lane 2: V26-3274.** Record the stalled-lookup row's red run. Ask the lead to put the `full-matrix` label on your pull request so the
macOS job runs on it, and include that job's timings for `tests.test_run_delivery_tracker` in your hand-off, beside the last `main` run's.

**Shell.** Never set `TMPDIR` under a checkout. Stop only processes you started, by the pid you recorded, never by pattern with `pkill`
or `killall`. Clear `skills/` bytecode before anything that builds a release.
