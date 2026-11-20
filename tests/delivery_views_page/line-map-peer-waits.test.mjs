// A wait on another delivery sharing the base is said where the thing that waits is shown: a planned story's on its
// stop and in "What's left", an idle lane's under its row, a queued piece's under its stage, and the lead's under the
// page's status line, as is what another delivery waits on in this one. The sentences are the state tool's, carried in
// the record's waits beside its own, and each is gone, with no reload, once the claim or the slot is released.
process.env.TZ = "UTC";

import { test } from "node:test";
import { JSDOM } from "jsdom";

import { REPLAYED_RUNS, builtinPage, expect, openPage, pageWithRuns } from "./support.mjs";

const NOW = "2026-11-17T14:20:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const lane = (id, state) => ({ id, title: id === "lane-1" ? "Waits" : "Renderer", focus: "", state, waitingOn: null, since: "2026-11-17T10:00:00Z" });

const CLAIM = "Waits for delivery PEER-1, whose PEER-2 claims skills/render.py.";
const LANE = "Lane 1 is next on APP-2, which waits for delivery PEER-1, whose PEER-2 claims skills/render.py.";
const PIECE = "APP-5 waits for delivery PEER-1, which is checking PEER-2, since 14:10 UTC.";
const LEAD = "The lead's push waits for delivery PEER-1, which is checking PEER-2, since 14:10 UTC.";
const HELD = "Delivery PEER-1 waits to start merging PEER-3.";

/** APP-2 planned in idle lane 1, naming the file PEER-1 claims; APP-5 queued in lane 2, waiting on PEER-1's check. */
function record() {
  const on = { delivery: "PEER-1", subject: "PEER-2" };
  return {
    state: {
      schemaVersion: "delivery-state/1", version: 3,
      meta: { headline: "Peers", updatedAt: NOW, baseHead: null, releasedHead: null, checking: { now: null, next: [] } },
      lanes: [lane("lane-1", "idle"), lane("lane-2", "working")],
      queue: [{ order: 1, subject: "APP-5", title: "Five", lane: "lane-2", stage: "queued", head: "b".repeat(40), syncedHead: null,
                leadInputs: [], reportedRounds: 1, runId: null, checks: [] }],
      laneOwnership: { "lane-1": [], "lane-2": [] },
      planned: [{ subject: "APP-2", title: "Say what a story waits on", lane: "lane-1", stage: "planned", files: ["skills/render.py"] }],
      finish: [], followups: [], leases: [], extensions: {},
    },
    journal: [],
    publication: {},
    waits: [
      { subject: "APP-2", on, cause: "claim", stage: "building", frees: "released", needsSomeone: false, words: CLAIM },
      { subject: "lane-1", on, cause: "claim", stage: "building", frees: "released", needsSomeone: false, words: LANE },
      { subject: "APP-5", on, cause: "slot", stage: "check", frees: "released", since: "2026-11-17T14:10:02Z", needsSomeone: false, words: PIECE },
      { subject: "lead", on, cause: "slot", stage: "check", frees: "released", since: "2026-11-17T14:10:02Z", needsSomeone: false, words: LEAD },
    ],
    waitedOn: [{ subject: "lead", by: [{ delivery: "PEER-1", subject: "PEER-3" }], words: HELD }],
  };
}
/** The same record once the claim and the slot are released, as the live server sends it. */
function released() {
  return { ...record(), waits: [], waitedOn: [] };
}

const stop = (app, key) => app.querySelector(`:is(.stop-btn, .mg-tick)[data-subject="${key}"]`);
const leftRow = (app, key) => [...app.querySelectorAll(".left-groups li")].find((row) => row.querySelector(`[data-ticket="${key}"]`));
const person = (app, id) => [...app.querySelectorAll(".person")].find((row) => row.querySelector("h3").textContent === (id === "lane-1" ? "Waits" : "Renderer"));
const said = (node) => (node ? node.textContent : "");

test("a wait on another delivery is said on the story, the lane, the queued piece and under the lead's status", async () => {
  const [shown, story, piece] = await openPage(pageWithRuns(record(), {}, { config: CONFIG }), { now: NOW, steps: [
    { click: ':is(.stop-btn, .mg-tick)[data-subject="APP-2"]' }, { click: ':is(.stop-btn, .mg-tick)[data-subject="APP-5"]' }] });
  // Under the page's status line: what the lead waits on in PEER-1, and what PEER-1 waits on here.
  expect([...shown.app.querySelectorAll(".say-wait")].map((line) => line.textContent)).toEqual([LEAD, HELD]);
  expect(said(person(shown.app, "lane-1"))).toContain(LANE);
  expect(stop(story.app, "APP-2").getAttribute("aria-label")).toContain(CLAIM);
  expect([...story.app.querySelectorAll("#detail .d-wait")].map((line) => line.textContent)).toEqual([CLAIM]);
  expect(said(leftRow(story.app, "APP-2"))).toContain(CLAIM);
  expect([...piece.app.querySelectorAll("#detail .d-wait")].map((line) => line.textContent)).toEqual([PIECE]);
});

test("released, every wait on another delivery is gone with no reload", async () => {
  const [, , gone] = await openPage(pageWithRuns(record(), {}, { config: CONFIG }), { now: NOW, steps: [
    { click: ':is(.stop-btn, .mg-tick)[data-subject="APP-2"]' }, { push: released() }] });
  const text = gone.app.textContent;
  for (const words of [CLAIM, LANE, PIECE, LEAD, HELD]) { expect(text.includes(words)).toBe(false); }
  expect(gone.app.querySelectorAll(".say-wait").length).toBe(0);
  expect(gone.app.querySelectorAll("#detail .d-wait").length).toBe(0);
});

test("the built-in page says the lead's waits under its status and each piece's and story's under its row", () => {
  const dom = new JSDOM(builtinPage(record()), { runScripts: "dangerously" });
  const waits = [...dom.window.document.querySelectorAll("#app .wait")].map((line) => line.textContent);
  dom.window.close();
  expect(waits).toEqual([LEAD, HELD, PIECE, CLAIM, LANE]);
  const freed = new JSDOM(builtinPage(released()), { runScripts: "dangerously" });
  expect(freed.window.document.querySelectorAll("#app .wait")).toHaveLength(0);
  freed.window.close();
});
