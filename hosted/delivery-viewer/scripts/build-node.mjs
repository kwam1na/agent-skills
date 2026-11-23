// Bundles the portable server, the core and its generated validators and assets into one module with no runtime
// dependencies beyond Node itself. Run after generate:core.
import { build } from "esbuild";
await build({
  entryPoints: [new URL("../src/node/server.ts", import.meta.url).pathname],
  outfile: new URL("../build/node/server.mjs", import.meta.url).pathname,
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  external: ["node:*"],
  legalComments: "none",
  logLevel: "warning",
});
