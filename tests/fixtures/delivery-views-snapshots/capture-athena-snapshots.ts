/**
 * Records what the existing TypeScript snapshot commands print for the
 * fixtures beside this file, so the Python snapshot kinds can be held to it.
 *
 *   bun capture-athena-snapshots.ts <checkout of the adopter repository> <this directory>
 *
 * The commands read Linear through an injected transport and gh through an
 * injected runner; nothing reaches the network. Writes athena-snapshots.json.
 */
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const [checkout, fixtures] = process.argv.slice(2);
if (!checkout || !fixtures) throw new Error("usage: bun capture-athena-snapshots.ts <checkout> <fixtures>");

const { runTrackerSnapshot } = await import(path.join(checkout, "scripts/orchestration-tracker-snapshot.ts"));
const { runForgeSnapshot } = await import(path.join(checkout, "scripts/orchestration-forge-snapshot.ts"));

const linear = JSON.parse(readFileSync(path.join(fixtures, "linear.json"), "utf8"));
const ghList = readFileSync(path.join(fixtures, "gh-pr-list.json"), "utf8");
const home = mkdtempSync(path.join(tmpdir(), "capture-home-"));
mkdirSync(path.join(home, ".config/linear"), { recursive: true });
writeFileSync(path.join(home, ".config/linear/api_key"), "lin_api_capturekey\n");
chmodSync(path.join(home, ".config/linear/api_key"), 0o600);

const transport = async ({ body }: { body: string }) => {
  const { variables } = JSON.parse(body);
  return { status: 200, body: variables.after === "cursor-1" ? linear["page-2"] : linear["page-1"] };
};
const now = () => new Date("2026-10-22T12:00:00.000Z");
const commands: string[][] = [];
const runCommand = async (command: string[]) => {
  commands.push(command);
  return { exitCode: 0, stdout: ghList, stderr: "" };
};

const tracker = await runTrackerSnapshot(["ENG-100"], { home, transport, now });
const forge = await runForgeSnapshot(["ENG-100", "--repo", "example-org/example-repo"], { home, transport, now, runCommand });
for (const result of [tracker, forge]) {
  if (result.code !== 0) throw new Error(result.stderr);
}
writeFileSync(path.join(fixtures, "athena-snapshots.json"), JSON.stringify({
  tracker: JSON.parse(tracker.stdout),
  forge: JSON.parse(forge.stdout),
  ghCommand: commands[0],
}, null, 2) + "\n");
