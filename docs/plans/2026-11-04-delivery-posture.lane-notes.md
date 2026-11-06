## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** The operator has given the lead authority to land this on `main`. No release is cut, and no lane tags anything.

**Ownership.** `skills/bootstrap-agent-harness/**` moved out of lane 1's files, so lane 1 doesn't touch `provenance.lock.json`. Its adoption notes go in `docs/product-generation.md` and
`README.md`. A lane that finds it needs a file outside its `owns` raises it with the lead before hand-off.

**Scenarios the plan's review added after it closed.**

- Lane 1: fetch failing, tracking ref at `focused`, private ref undeclared and then malformed: `gate` both ways. When both refs are non-`focused` with different codes, the tracking ref's code wins. On
  a built product, name the commit `product-runtime/` was built from, and prove it by rebuilding from that commit and comparing bytes.
- Lane 2: rebuild `plan.json` on any append to the full-health log, not only a skip.

**Lane 1: the source pull request.** Work in a worktree of `/Users/kwamina/agent-delivery-harness`, under its `.worktrees/`, as its `docs/delivery-runbook.md` section 1 says: `npm install`, from `origin/main`,
and leave its other worktrees and untracked files alone. Its branch is `claude/v26-3124-delivery-posture`, and its pull request is titled `[V26-3124]: ...`. This delivery uses the plan's sensors there, not that
repository's run and gate commands, because the plan's posture is what this ticket adds and that repository's CI has been red since before this work. Review the pull request's diff with the two lenses as subagents
until both approve, then send the lead its link and head. Don't merge it: the lead does, and tells you the merge commit. Only then cherry-pick that commit onto `fa63884` as `runtime/0.7.8`, push that branch, build, and
open this repository's pull request, titled `V26-3124: ...`, with `run-delivery`. Hand off once the lead says V26-3125 has merged.

**Lane 2.** Open the pull request titled `V26-3125: ...`. Hand off when ready: V26-3125 merges first.

**Shell.** Never set `TMPDIR` under a checkout. Stop only processes you started, by recorded pid.
