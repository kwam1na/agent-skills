# V26-3775 lane notes

- **The base carries the chapter-pages redesign.** `integrate/plan-preview-views` holds
  #488 (the live view as one page per chapter, the empty states, and `richWords` in
  `run-summary.js`) as well as `main`. The plan's "Current state" was checked against
  `main` at `7dd81d29`, before that redesign. Read `line-map.js`, `line-map.css`,
  `run-summary.js`, `views_common.py` and `render.py` as they stand on the base, not as
  the plan quotes them. Where a function the plan names has moved or been renamed, follow
  the plan's intent on the base and say so in the hand-off.
- **The plan chapter is now a page.** A preview's plan chapter is drawn as the first
  chapter page with the chapters tab. The "never reads as approved" checks cover what
  that page and its tab entry show, as well as the chapter body.
- **Build hooks.** `build-assets.mjs` patches `line-map.js` by exact text. Run
  `hosted.build` and `hosted.browser` on any change to `skills/delivery-views/assets/**`.
- **Shared outputs.** Refresh `catalog.json`, the selected `profiles/` and
  `provenance.lock.json` through `scripts/update-provenance.py` in each story's own
  change. On a conflict confined to the lock, take either side and run the writer.
- **Processes and ports.** Tests bind port 0 or set `RUN_DELIVERY_LIVE_PORT=0`, stop what
  they start by recorded pid, and mark live-server classes `run_quietly`.
