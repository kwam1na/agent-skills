#!/usr/bin/env node
// node hosted/conformance/cli.mjs --origin <origin> --credentials <file> [--slow] [--fixtures <directory>]
// The credentials file is JSON: [{"repository": "<id>", "token": "<token>"}, {...}], two repositories, read once.
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { runSuite } from "./suite.mjs";

const usage = "usage: cli.mjs --origin <origin> --credentials <file> [--slow] [--fixtures <directory>]";
let options;
try {
  options = parseArgs({ options: { origin: { type: "string" }, credentials: { type: "string" }, slow: { type: "boolean" },
    fixtures: { type: "string" } } }).values;
} catch (error) {
  console.error(`${error.message}\n${usage}`);
  process.exit(2);
}
if (!options.origin || !options.credentials) {
  console.error(usage);
  process.exit(2);
}
let credentials;
try {
  credentials = JSON.parse(readFileSync(options.credentials, "utf8"));
} catch {
  console.error(`the credentials file is not readable JSON: ${options.credentials}`);
  process.exit(2);
}
let results;
try {
  results = await runSuite({ origin: options.origin, credentials, slow: !!options.slow, fixtures: options.fixtures,
    report: (row) => console.log(row.ok ? `ok ${row.name}` : `not ok ${row.name}: ${row.message}`) });
} catch (error) {
  console.error(error.message);
  process.exit(2);
}
const failed = results.filter((row) => !row.ok);
console.log(`${results.length - failed.length} of ${results.length} scenarios passed${options.slow ? "" : " (fast tier)"}`);
process.exit(failed.length ? 1 : 0);
