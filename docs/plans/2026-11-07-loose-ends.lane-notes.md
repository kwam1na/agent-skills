## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `main`. No lane merges, marks a pull request
ready, tags, or applies a release. The lead cuts and pushes `v1.6.0-rc.3`, with the operator's approval already given.

**Ownership.** Lane 1 holds the run-delivery chain throughout. Lane 2 holds the merge queue and the views in tier 1, plus
`.agent-skills/` and `.agents/orchestration.json` for V26-3182. `orchestration-guide.md` passes to lane 1 only after V26-3197 merges,
and lane 2's views pass to lane 1 for V26-3242 only after V26-3164 merges. `provenance.lock.json` moves to the lane being accepted
immediately before its `lead-accept`. A lane that finds it needs a file outside its `owns` raises it with the lead before hand-off.

**V26-3182 (lead's correction to the plan).** The record can't hold a lead-only story, so V26-3182 is lane 2's, last, once the lead
says rc.3 is published. Its pull request carries two commits: the apply at `.agent-skills/current` first, then the binding change. It
waits for the lead's word to start.

**Lane 1: V26-3232 first.** Reproduce before you change anything, and record the red run. Ask the lead to put the `full-matrix` label
on your pull request so the macOS job runs on it, and include that job's run in your hand-off.

**Lane 2: V26-3164.** The effort to reproduce is bounded at five page-lane runs under load on macOS, plus one Ubuntu attempt through a
`full-matrix`-free CI run on your draft pull request. If it doesn't reproduce, close with the evidence and the hypothesis, per the plan.

**Shell.** Never set `TMPDIR` under a checkout. Stop only processes you started, by the pid you recorded, never by pattern. Clear
`skills/` bytecode before anything that builds a release.
