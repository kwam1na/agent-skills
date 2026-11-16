// orchestrate-work's built-in page prints the story's beats, newest chapter first, in the registry's words.
process.env.TZ = "UTC";

import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { JSDOM } from "jsdom";

import { ROOT, builtinPage, expect } from "./support.mjs";

/** A record as the state tool shows it, holding one of the narrator's journals. */
function shown(name) {
  const folder = path.join(ROOT, "tests", "fixtures", "narrator", name);
  const journal = readFileSync(path.join(folder, "journal.jsonl"), "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const state = {
    schemaVersion: "delivery-state/1", version: 1,
    meta: { headline: "A delivery", updatedAt: "2026-11-16T12:00:00Z", checking: { now: null, next: [] } },
    lanes: [], queue: [], planned: [], finish: [], followups: [], leases: [],
  };
  return { state, journal };
}
function drawn(page) {
  const dom = new JSDOM(page, { runScripts: "dangerously" });
  const app = dom.window.document.getElementById("app");
  const said = app.textContent;
  dom.window.close();
  return said.slice(said.indexOf("Story"));
}

test("the built-in page draws the story: its digest, then each chapter newest first with its beats", () => {
  const document = shown("break-stops-a-piece");
  const said = drawn(builtinPage(document));
  expect(said.startsWith("StoryOne piece landed so far. One setback cost 17m.")).toBe(true);
  // Newest first: the landing's chapter, then the start.
  expect(said.indexOf("A landed") < said.indexOf("Getting started")).toBe(true);
  for (const words of ["The main line was repaired", "A was stopped", "The main line broke"]) {
    expect(said).toContain(words);
  }
  expect(said.includes("candidate.stopped")).toBe(false);
});

test("with the registry's path at a copy with a changed head, the built-in page shows the changed words", () => {
  const registry = JSON.parse(readFileSync(path.join(ROOT, "skills", "orchestrate-work", "references", "journal-kinds.json"), "utf8"));
  registry.kinds["base.repaired"].words.head = "The main line was mended";
  const planted = path.join(mkdtempSync(path.join(tmpdir(), "planted-registry-")), "journal-kinds.json");
  writeFileSync(planted, JSON.stringify(registry));
  const said = drawn(builtinPage(shown("break-stops-a-piece"), planted));
  expect(said).toContain("The main line was mended");
  expect(said.includes("The main line was repaired")).toBe(false);
});
