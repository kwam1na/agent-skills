## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**The base is `integrate/tracker-and-lead-awake`, not `main`.** Start each story with `lane-start <key> --base
integrate/tracker-and-lead-awake`, and open each draft pull request into that branch. Never merge `main` or the integration base into
a story branch. V26-3406 is changing the same files on `main` right now. The lead brings this delivery to `main` in one reviewed pull
request after V26-3406 closes (Decision 1), so no lane resolves conflicts with `main`.

**Authority.** The lane may push its story branches and open draft pull requests into the integration base, as the delivery's grant
states. The lane never merges, marks a pull request ready, tags, applies a release, or pushes to `main` or the integration base.

**No deferrals.** Nothing found in this delivery is deferred. Every finding is fixed in the story that found it, before hand-off,
whatever its severity, cost or round. This overrides the brief's Triage and Hand-off sections where they say otherwise:
- A P2 or P3 a lens files as deferred is fixed in the lane, the same as a blocking finding.
- A late P1 listed in `lateFindings` is fixed too.
- A note a lens leaves in its evidence (for example "P2, not filed") is a finding, and is fixed.
- Nothing goes to the lead as an expensive non-blocking finding: `handoff` carries no `--findings`, and no follow-up item is filed
  for anything this delivery found.

If a fix needs a file outside your `owns`, ask the lead to move it to you. If a fix is out of reach (it needs an operator decision, or
another epic's change), stop and tell the lead before hand-off. Don't hand off around it.

**Criteria.** Each tracker item's Acceptance Criteria are the plan section's, word for word. The plan's Decisions say how each is met.

**Node on your own `PATH`.** This machine's nvm default is Node 20.19, and the launcher rows need Node 22. Put
`~/.nvm/versions/node/v22.23.3/bin` first on `PATH` in every shell you run tests or `run_delivery.py` from. Once V26-3260 merges,
`lane-start` itself refuses a Node below the minimum.

**Rows never touch the real delivery.** Supervisor and live-view rows run in their own temporary folders with their own state
directories, follow the plan's Shared resources section (`RUN_DELIVERY_LIVE_PORT=0`, `run_serially` for fixed ports, stub `node` and
`python3` on a temporary `PATH`, a stub nvm under a temporary `HOME`), and stop only the pids they recorded. Never signal this
delivery's or V26-3406's supervisor or live view.

**Release surface.** `run-delivery` and `orchestrate-work` are releasable skills. Every change to them updates their catalog entries,
every selected profile, and the provenance output digests in the same commit, through `scripts/update-provenance.py`. A local suite run
can leave bytecode under `skills/`; clear it before the provenance check (AGENTS.md).

**Lane 1: V26-3260, then V26-3262.** V26-3262 starts from the integration base once V26-3260 has merged into it.
