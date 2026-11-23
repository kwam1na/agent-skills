// Versions move together: the conformance suite's constants equal the core's and the spec's, so a change to any of
// them moves the spec, the suite and the servers in the same change.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import * as suite from "../../conformance/versions.mjs";
import * as core from "../src/protocol";

const text = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const spec = text("../../protocol/hosted-delivery-api-1.md");
const schema = (path: string) => JSON.parse(text(path)).properties.schemaVersion.const;
/** The value cell of a row in the spec's "Versions and bounds" table. */
function row(name: string): string {
  const table = spec.slice(spec.indexOf("## Versions and bounds"), spec.indexOf("## Identifiers"));
  const line = table.split("\n").find((entry) => entry.startsWith(`| ${name} |`));
  if (!line) throw new Error(`the spec has no "${name}" row`);
  return line.split("|")[2].trim();
}
/** A bound's number: the byte count in parentheses when the cell gives one, else its first number. */
const number = (cell: string) => Number((/\(([\d ]+) bytes\)/.exec(cell) ?? /(\d[\d ]*)/.exec(cell))![1].replace(/ /g, ""));

describe("versions move together", () => {
  it("the suite's API, snapshot and renderer versions are the core's, the schemas' and the spec's", () => {
    expect(suite.API_VERSION).toBe(core.API_VERSION);
    expect(suite.RENDERER_VERSION).toBe(core.RENDERER);
    expect(suite.LEGACY_RENDERER_VERSION).toBe(core.LEGACY_RENDERER);
    expect(suite.SNAPSHOT_VERSION).toBe(schema("../../../skills/delivery-views/references/hosted-snapshot.schema.json"));
    expect(suite.LEGACY_SNAPSHOT_VERSION).toBe(schema("../compat/v2/hosted-snapshot.schema.json"));
    expect(row("API")).toBe(`\`${core.API_VERSION}\``);
    expect(row("Snapshot schema")).toBe(`\`${suite.SNAPSHOT_VERSION}\`, rendered by \`${core.RENDERER}\``);
    expect(row("Retained snapshot schema")).toBe(`\`${suite.LEGACY_SNAPSHOT_VERSION}\`, rendered by \`${core.LEGACY_RENDERER}\``);
  });
  it("the suite's bounds are the core's and the spec's", () => {
    const pairs: [string, number, number][] = [
      ["Lease", suite.LEASE_MS, core.LEASE_MS], ["Staleness", suite.STALE_MS, core.STALE_MS],
      ["Snapshot body", suite.MAX_SNAPSHOT_BYTES, core.MAX_BYTES], ["Session body", suite.MAX_SESSION_BYTES, core.MAX_SESSION_BYTES],
      ["Confirmation body", suite.MAX_CONFIRM_BYTES, core.MAX_CONFIRM_BYTES], ["JSON nesting", suite.MAX_NESTING, core.MAX_NESTING],
    ];
    for (const [name, ours, theirs] of pairs) {
      expect(ours, name).toBe(theirs);
      expect(number(row(name)), name).toBe(theirs);
    }
  });
});
