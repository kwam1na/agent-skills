# V26-3747 lane notes

- Each lane delivers its stories in the plan's order, each handed off before the next starts. Lane 2 starts
  V26-3752 only once V26-3748 has merged into `integrate/portable-hosted-service`, and validates on a base
  containing it.
- The plan's "Decisions recorded here" bind every story. Read the ones your story names before starting; a change
  that departs from one goes to the lead as a plan question, not into the diff.
- The protocol does not change. No test-only route, clock hook or header is added to the service, and the
  publisher's transport and origin rules stay as they are.
- Lane 1 gains `hosted/delivery-viewer/` when V26-3748 merges, and `hosted/delivery-viewer/src/node/` and
  `hosted/delivery-viewer/Dockerfile` when V26-3749 merges. Never edit outside your claims, docs included.
- `build-assets.mjs` patches `skills/delivery-views/assets/line-map.js` by exact text. Keep its hooks and its
  `hosted.asset_hook_drift` refusal (see `docs/solutions/integration-issues/`).
- Each new test module is catalogued in `.agents/orchestration.json` in the same diff, and named in your
  `handoff --checks`. A change to `SERVICE_IDS` or the hosted readers updates the integration recipe in
  `docs/own-deliveries.md` in the same change.
- Python tests stay inside `tests/suite-budget.json`: add assertions to existing real runs rather than new ones.
- Servers a test starts are stopped by their recorded pid, never by pattern. Free loopback ports only.
- Docker is optional locally: report it as unavailable where it is missing, never as passed.
- No release is cut and no tag is made by a lane. The candidate release is operator work after close-out.
