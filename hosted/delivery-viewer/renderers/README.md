# Retained hosted renderers

The index records the source revision and archive digest for each retained renderer. Each archive contains its trusted release shell and all of its immutable assets, including fonts and licences. The build serves duplicate asset paths once.

To archive a renderer before replacing it, check out the deployed source revision in its own worktree. From its `hosted/delivery-viewer` directory run `npm ci`, `node scripts/generate-validator.mjs`, and `node scripts/build-assets.mjs` with its pinned Node version. From the new service checkout run the archive command and record its printed SHA-256 and the original revision in `index.json`.

```sh
node scripts/archive-renderer.mjs /path/to/release/hosted/delivery-viewer/src/generated-assets.ts renderers/v2.json.gz
```

Verify the asset URLs against the deployed shell and exercise the retained renderer with a stored snapshot from that generation before deploying. Preserve existing archives so saved links and cached pages can still load their assets.
