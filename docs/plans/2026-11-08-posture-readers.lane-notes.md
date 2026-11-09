## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `main`, as the delivery's grant states. Lane 1 may
also push its harness branch and open a draft pull request into the harness's `main`, and push `runtime/0.8.3` there. No lane merges, marks
a pull request ready, tags, or applies a release.

**No deferrals.** Nothing found in this delivery is deferred. Every finding is fixed in the story that found it, before hand-off, whatever
its severity, cost or round. This overrides the brief's Triage and Hand-off sections where they say otherwise:
- A P2 or P3 a lens files as deferred is fixed in the lane, the same as a blocking finding.
- A late P1 listed in `lateFindings` is fixed too.
- A note a lens leaves in its evidence (for example "P2, not filed") is a finding, and is fixed.
- A finding first raised in a later round is fixed, not judged on scope.
- `handoff` carries no `--findings`, and no follow-up item is filed for anything this delivery found.

If a fix needs a file outside your `owns`, ask the lead to move it to you. If a fix is out of reach (it needs an operator decision, or
another epic's change), stop and tell the lead before hand-off.

**Other deliveries on `main`.** V26-3304 and V26-3250 are live and change `skills/run-delivery/`, `tests/test_run_delivery.py` and
`provenance.lock.json` too. Never merge or rebase onto the base yourself. If the lead tells you a sync conflicted, the lead says what to do.
Keep your changes to run-delivery files small and local to the functions your story names, so syncs merge cleanly.

**Lane 1: V26-3162.** Two repositories. Work the harness change in a checkout of `kwam1na/agent-delivery-harness` under
`/Users/kwamina/delivery-harness-worktrees/`, never in `/Users/kwamina/agent-delivery-harness` itself, which other work uses. Record the
per-test comparison the plan names on both the pull request and `runtime/0.8.3`. Hand off the harness pull request to the lead for review
and merge before you build `runtime/0.8.3`; the build's story branch here is what goes through the queue.

**Lane 2: V26-3174, then V26-3163.** Work the run-delivery findings first. Don't touch the built-product test files until the lead tells
you they've moved to you, after V26-3162 merges.

**Shell.** Never set `TMPDIR` under a checkout. Stop only processes you started, by the pid you recorded, never by pattern with `pkill`
or `killall`. Clear `skills/` bytecode before anything that builds a release.
