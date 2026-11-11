## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `main`, as the delivery's grant states. No lane
merges, marks a pull request ready, tags, or applies a release, except V26-3410's apply of the candidate the lead has tagged.

**No deferrals.** Nothing found in this delivery is deferred. Every finding is fixed in the story that found it, before hand-off,
whatever its severity, cost or round. This overrides the brief's Triage and Hand-off sections where they say otherwise:
- A P2 or P3 a lens files as deferred is fixed in the lane, the same as a blocking finding.
- A late P1 listed in `lateFindings` is fixed too.
- A note a lens leaves in its evidence (for example "P2, not filed") is a finding, and is fixed.
- Nothing goes to the lead as an expensive non-blocking finding: `handoff` carries no `--findings`, and no follow-up item is filed
  for anything this delivery found.

If a fix needs a file outside your `owns`, ask the lead to move it to you. If a fix is out of reach (it needs an operator decision, or
another epic's change), stop and tell the lead before hand-off. Don't hand off around it.

**Criteria come from the tracker items.** Each item's Acceptance Criteria were created from its plan section and then gained the rows
the plan review's second round left as notes. Where the two differ, the tracker item is the fuller one and binds:
- V26-3409 has an orientation row for every `SessionStart` source (`startup`, `resume`, `clear`, `compact` and `fork`), and one with
  the payload's `cwd` reached through a symlink.
- V26-3410's live check runs the installed command unchanged behind a logging wrapper that logs stdin first.

**Pins that will go stale.** Re-derive the pins your change makes stale, as the plan's Units and order lists them, and name each in your
hand-off. `provenance.lock.json` is lane 2's: add `skills/run-delivery/hooks.json` as an output by hand, then digest it with the writer.

**No real host in rows.** No test starts a Claude Code session or writes `.claude/settings.json` in the repository root. Every lifecycle
and hook row works in its own temporary folder with its own state directory.

**Lane 1: V26-3407, then V26-3408, then V26-3410 in tier 2.** V26-3410 starts only when the lead says the minor candidate is tagged and
published; it applies it and turns `lead.may-stop` to `enforce` in one change, and records the live check on the epic before hand-off.

**Lane 2: V26-3270, then V26-3409.** V26-3409 starts once V26-3407 has merged, since it needs the manifest schema and the dispatcher.
