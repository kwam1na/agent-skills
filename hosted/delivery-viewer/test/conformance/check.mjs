// hosted.conformance: the conformance suite against the built Worker, then against every planted-failure server.
//   node test/conformance/check.mjs [--slow]
// The built Worker must pass every scenario (with --slow, the slow tier too). A copy built from the unchanged source
// must pass the fast tier, so the builder alone breaks nothing. Each planted server must start and fail the fast tier in
// exactly the scenarios it names. Run `npm run build` first: this reads dist/worker.js and the generated sources.
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runSuite } from "../../../conformance/suite.mjs";
import { builtWorker, credentials, startWorker } from "../launcher.mjs";
import { PLANTED, buildWorker } from "./planted.mjs";

const slow = process.argv.includes("--slow");
// Kept here rather than read from the suite, so a scenario dropped from it, or a slow tier that never ran, is red.
const FAST = ["round-trip.first-repository", "round-trip.second-repository", "host-headers-ignored", "exact-retry",
  "sequence-rules", "writer-fencing", "repository-scope", "errors.400", "errors.401", "errors.404", "errors.405", "errors.409",
  "errors.413", "errors.415", "errors.422", "projection-fixtures", "streamed-body-limit", "refused-requests",
  "no-redirect-listing-or-history", "anonymous-read", "security-headers"];
const SLOW = ["slow.lease-expiry", "slow.resume", "slow.takeover", "slow.staleness"];
const CLI = fileURLToPath(new URL("../../../conformance/cli.mjs", import.meta.url));
const unknown = process.argv.slice(2).filter((arg) => arg !== "--slow");
if (unknown.length) {
  console.error(`usage: check.mjs [--slow]; unknown: ${unknown.join(" ")}`);
  process.exit(2);
}
if (!existsSync(builtWorker)) {
  console.error(`no built Worker at ${fileURLToPath(builtWorker)}: run npm run build first`);
  process.exit(2);
}

/** Starts a server, runs the suite and stops the server. A server that fails to start is reported, never skipped. */
async function against(label, script, tier) {
  let server;
  try {
    server = await startWorker({ script });
  } catch (error) {
    return { started: false, error: `${label} did not start: ${error?.message ?? error}` };
  }
  try {
    const results = await runSuite({ origin: server.origin, credentials, slow: tier === "slow" });
    return { started: true, results, failed: results.filter((row) => !row.ok) };
  } finally {
    await server.dispose();
  }
}

const problems = [];
const worker = await against("the built Worker", undefined, slow ? "slow" : "fast");
if (!worker.started) problems.push(worker.error);
else {
  for (const row of worker.results) console.log(row.ok ? `ok ${row.name}` : `not ok ${row.name}: ${row.message}`);
  if (worker.failed.length) problems.push(`the built Worker failed ${worker.failed.map((row) => row.name).join(", ")}`);
  const ran = worker.results.map((row) => row.name);
  const expected = slow ? [...FAST, ...SLOW] : FAST;
  if (JSON.stringify(ran) !== JSON.stringify(expected))
    problems.push(`the built Worker ran ${ran.join(", ")}; the check expects exactly ${expected.join(", ")}`);
}

/** The adopter's entry point: cli.mjs must exit 0 against the Worker and 1 against a planted server. */
async function cliExit(label, script) {
  const folder = mkdtempSync(join(tmpdir(), "hosted-conformance-cli-"));
  let server;
  try {
    const file = join(folder, "credentials.json");
    writeFileSync(file, JSON.stringify(credentials), { mode: 0o600 });
    server = await startWorker({ script });
    const run = spawnSync(process.execPath, [CLI, "--origin", server.origin, "--credentials", file],
      { encoding: "utf8", timeout: 300_000 });
    return run.status;
  } catch (error) {
    problems.push(`the CLI run against ${label} did not start: ${error.message}`);
    return null;
  } finally {
    await server?.dispose();
    rmSync(folder, { recursive: true, force: true });
  }
}
const cliWorker = await cliExit("the built Worker");
if (cliWorker !== 0) problems.push(`cli.mjs exited ${cliWorker} against the built Worker, not 0`);
const cliPlanted = await cliExit("a planted server", await buildWorker(PLANTED[0].patches));
if (cliPlanted !== 1) problems.push(`cli.mjs exited ${cliPlanted} against planted ${PLANTED[0].name}, not 1`);
if (cliWorker === 0 && cliPlanted === 1) console.log(`ok cli: exits 0 against the Worker and 1 against planted ${PLANTED[0].name}`);

let control;
try {
  control = await against("the control copy", await buildWorker(), "fast");
} catch (error) {
  control = { started: false, error: `the control copy did not build: ${error.message}` };
}
if (!control.started) problems.push(control.error);
else if (control.failed.length)
  problems.push(`the unchanged copy failed ${control.failed.map((row) => `${row.name} (${row.message})`).join(", ")}`);
else console.log("ok control: an unchanged copy of the source passes the fast tier");

for (const planted of PLANTED) {
  let outcome;
  try {
    outcome = await against(`planted ${planted.name}`, await buildWorker(planted.patches), "fast");
  } catch (error) {
    outcome = { started: false, error: `planted ${planted.name} did not build: ${error.message}` };
  }
  if (!outcome.started) {
    problems.push(outcome.error);
    console.log(`not ok planted ${planted.name}: ${outcome.error}`);
    continue;
  }
  const failed = outcome.failed.map((row) => row.name).sort();
  const expected = [...planted.fails].sort();
  if (JSON.stringify(failed) === JSON.stringify(expected)) {
    console.log(`ok planted ${planted.name}: without "${planted.rule}" it fails ${expected.join(", ")}`);
    continue;
  }
  const detail = outcome.failed.map((row) => `${row.name}: ${row.message}`).join("; ") || "nothing";
  problems.push(`planted ${planted.name} should fail exactly ${expected.join(", ")}; it failed ${detail}`);
  console.log(`not ok planted ${planted.name}: expected to fail ${expected.join(", ")}, failed ${failed.join(", ") || "nothing"}`);
}

if (problems.length) {
  console.error(`\nhosted.conformance failed:\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`\nhosted.conformance passed: the Worker${slow ? " (both tiers)" : ""}, the control and ${PLANTED.length} planted servers`);
