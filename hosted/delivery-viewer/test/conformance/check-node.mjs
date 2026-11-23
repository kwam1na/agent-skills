// The conformance suite against the built portable server: node test/conformance/check-node.mjs [--slow]
// Run `npm run build:node` first. The server runs as its own process on a fresh data directory, with the
// launcher's synthetic credentials; it must cover exactly the listed scenarios and pass every one, and
// hosted/conformance/cli.mjs must exit 0 against it.
import { spawnSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runSuite } from "../../../conformance/suite.mjs";
import { builtNode, credentials, startNode } from "../launcher.mjs";
import { FAST, SLOW } from "./scenarios.mjs";

const slow = process.argv.includes("--slow");
const unknown = process.argv.slice(2).filter((arg) => arg !== "--slow");
if (unknown.length) {
  console.error(`usage: check-node.mjs [--slow]; unknown: ${unknown.join(" ")}`);
  process.exit(2);
}
if (!existsSync(builtNode)) {
  console.error(`no built server at ${fileURLToPath(builtNode)}: run npm run build:node first`);
  process.exit(2);
}
const problems = [];
let server;
try {
  server = await startNode();
} catch (error) {
  console.error(`the portable server did not start: ${error.message}`);
  process.exit(1);
}
try {
  const results = await runSuite({ origin: server.origin, credentials, slow,
    report: (row) => console.log(row.ok ? `ok ${row.name}` : `not ok ${row.name}: ${row.message}`) });
  const failed = results.filter((row) => !row.ok);
  if (failed.length) problems.push(`the portable server failed ${failed.map((row) => row.name).join(", ")}`);
  const expected = slow ? [...FAST, ...SLOW] : FAST;
  if (JSON.stringify(results.map((row) => row.name)) !== JSON.stringify(expected))
    problems.push(`the run covered ${results.map((row) => row.name).join(", ")}; the check expects exactly ${expected.join(", ")}`);
  const file = join(server.dataDir, "conformance-credentials.json");
  writeFileSync(file, JSON.stringify(credentials), { mode: 0o600 });
  const cli = spawnSync(process.execPath, [fileURLToPath(new URL("../../../conformance/cli.mjs", import.meta.url)),
    "--origin", server.origin, "--credentials", file], { encoding: "utf8", timeout: 300_000 });
  if (cli.status !== 0) problems.push(`cli.mjs exited ${cli.status} against the portable server, not 0`);
  else console.log("ok cli: exits 0 against the portable server");
} finally {
  const { code, signal } = await server.stop("SIGTERM");
  if (code !== 0) problems.push(`the portable server exited ${code ?? signal} on SIGTERM, not 0`);
  await server.dispose();
}
if (problems.length) {
  console.error(`\nconformance against the portable server failed:\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`\nconformance passed against the portable server${slow ? " (both tiers)" : ""}`);
