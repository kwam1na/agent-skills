# Amendment 1: the delivery runs on the workflow's own runner

The operator directed this after plan review. The reviewed plan stays byte for byte as its review evidence bound it, and
this amendment overrides it where they differ.

- **Runner.** The delivery uses `merge_queue.py serve`, not a lead acting as the runner. `.agents/orchestration.json` and
  `.agents/delivery-views.json` live on the delivery base, whose tip is where the runner reads them. The plan's "Runner"
  contract decision and its "An orchestration binding" out-of-scope line no longer apply to the delivery base.
- **Not on the default branch.** The binding names the delivery base, and its paths are this machine's. The integration
  change request removes both files, so the default branch gains no binding. A standing one is a separate decision.
- **Expected red.** The binding sets `selection.lead`, and handoffs need acceptance. The lead checks each hand-off, selects
  the candidate's checks from its unit's declared sensors, then accepts it. `validate-registry` always runs. The full suite
  runs at close-out on the final base and at the integration change request.
- **Lanes.** Two lane sessions are reused across tickets, so the lead's message is what assigns the next one. Lane order
  follows the plan's dependencies, except that offline Linear qualification starts alongside the Green CI scout, since the
  two share no file; a scout failure routed to it reaches its lane as a message.
