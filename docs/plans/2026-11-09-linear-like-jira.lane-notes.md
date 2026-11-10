## This delivery's own rules

The approved plan's Decisions are binding. These notes add what the plan leaves to the lead.

**Authority.** Lanes may push their story branches and open draft pull requests into `main`, as the delivery's grant states. No lane
merges, marks a pull request ready, tags, or applies a release.

**No deferrals.** Nothing found in this delivery is deferred. Every finding is fixed in the story that found it, before hand-off,
whatever its severity, cost or round. This overrides the brief's Triage and Hand-off sections where they say otherwise:
- A P2 or P3 a lens files as deferred is fixed in the lane, the same as a blocking finding.
- A late P1 listed in `lateFindings` is fixed too.
- A note a lens leaves in its evidence (for example "P2, not filed") is a finding, and is fixed.
- Nothing goes to the lead as an expensive non-blocking finding: `handoff` carries no `--findings`, and no follow-up item is filed
  for anything this delivery found.

If a fix needs a file outside your `owns`, ask the lead to move it to you. If a fix is out of reach (it needs an operator decision, or
another epic's change), stop and tell the lead before hand-off. Don't hand off around it.

**Carried from the plan review.** The plan review's last round left two notes that bind V26-3269's rows:
- The error rows name each outcome: an authentication code is transient with no ledger success line; a forbidden code is permanent
  for that ticket and transient when the whole site refuses too, through `site_refuses()`; any other error is a permanent `failed`.
- The failure-class rows read "another 4xx, except 401 and 403, permanent".

**Carried from the last round, for V26-3334.** Decision 5's token pin forbids any `== "host"` comparison in a shipped script, yet the
refusals must recognise a host publisher. Detect it generically: a publisher entry that has a `kind` and no `argv` is refused, naming the
fix, so no host token is needed. The reopen code's old-file path constant stays the one named exception.

**Acceptance readings.** The lead recorded on V26-3269 that its AC3 is met by the publisher's existing `skip` outcome (Decision 3), and
on V26-3083 that its AC3 means no open entity is left (Decision 7). Review against those readings.

**Real Linear.** Rows never reach Linear. The live checks the plan names (V26-3269's marker round trip before it hands off, and
V26-3333's finish-line evidence after the switch) use `LINEAR_API_KEY` from your environment against an item of this delivery, and are
recorded on the item they belong to. Never write the key, or a payload carrying it, anywhere in the repository.

**Lane 1: V26-3269, then V26-3334, then V26-3333 in tier 2.** V26-3334 removes the host publisher mode (Decision 5); name every host row
you delete or replace in its hand-off. V26-3333 starts only when the lead says the major release candidate is tagged and published; it
applies it and switches the binding in one change, and the lead drains and acks the host outbox immediately before it merges.

**Lane 2: V26-3097, then V26-3083.** V26-3083's routine is created by the lead. Hand off the documentation with its pins; the lead
records the two run results on the item.

**Another delivery.** V26-3250 runs on `main` at the same time, and its lane 1 holds `skills/run-delivery/`, `skills/orchestrate-work/`,
`skills/delivery-views/` and their tests, and its lane 2 holds `.agents/orchestration.json`, `docs/own-deliveries.md` and `tests/test_own_deliveries.py`. Work
and review freely. When your story is ready, hand off as usual: the lead holds the queue on a shared file until the other delivery's
candidate on it has merged, and may send your story back for a new round if a merge moved its files. Never merge `main` into your
branch yourself, and rebase only when git reports a conflict.

**Suites.** No full suite in a lane. Run the affected modules with `.venv/bin/python scripts/run-tests.py -p <module>`. Reviewer
subagents never run a full suite either; say so in every reviewer brief you extend.
