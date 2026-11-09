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

**Ownership.** Lane 1 holds the run-delivery chain. Lane 2 holds the state tool, the merge queue and the views until V26-3259 merges,
when they pass to lane 1. Lane 2 takes the binding for V26-3309. `provenance.lock.json` moves to the lane the lead is accepting. A lane that finds it needs a file outside its
`owns` raises it with the lead before hand-off.

**Other deliveries.** V26-3304 and V26-3267 run on `main` at the same time and change some of lane 1's files. Work and review freely.
When your story is ready to hand off, hand off as usual: the lead holds the queue on a shared file until the other delivery's candidate
on it has merged, and may send your story back for a new round if a merge moved its files. Never merge `main` into your branch yourself.

**Lane 1: V26-3276, then V26-3256, V26-3263 and V26-3257 in tier 2.** Tier 2 starts once V26-3259 merges and its files pass to you.
Decision 1 binds V26-3276 and V26-3256: build V26-3256's one-command add on V26-3276's refusal. Never run a
`lead-*` command, or a test module, from the main checkout: only from your story's own checkout.

**Lane 2: V26-3259.** Keep the catalogue additive (Decision 7): every existing binding and JSON-list selection stays valid, proved by a
row. Don't edit `.agents/orchestration.json` for V26-3259: this repository's adoption is V26-3309, yours in tier 3, after the lead
tags and applies the release.

**Shell.** Never set `TMPDIR` under a checkout. Stop only processes you started, by the pid you recorded, never by pattern with `pkill`
or `killall`. Clear `skills/` bytecode before anything that builds a release.
