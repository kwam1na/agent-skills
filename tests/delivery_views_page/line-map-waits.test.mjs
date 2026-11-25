// What a still thing waits on is said where the thing is shown: on the stop's label, under its stage in the detail
// panel, in "What's left", and under an idle lane whose next story waits; the story others wait on names them. The
// sentences are the state tool's, printed as they are, and each is gone, with no reload, once what frees it happens.
process.env.TZ = "UTC";

import { test } from "node:test";
import { JSDOM } from "jsdom";

import { REPLAYED_RUNS, builtinPage, expect, openPage, pageWithRuns } from "./support.mjs";

const NOW = "2026-11-17T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const lane = (id, state) => ({ id, title: id === "lane-1" ? "Waits" : "Renderer", focus: "", state, waitingOn: null, since: "2026-11-17T10:00:00Z" });

/** APP-1 is in review in lane 2, its second round in, holding render.py; APP-2 is planned in idle lane 1 after it. */
function record() {
  return {
    state: {
      schemaVersion: "delivery-state/1", version: 3,
      meta: { headline: "Waits", updatedAt: NOW, baseHead: null, releasedHead: null, checking: { now: null, next: [] } },
      lanes: [lane("lane-1", "idle"), lane("lane-2", "reviewing")], queue: [],
      laneOwnership: { "lane-1": [], "lane-2": ["render.py"] },
      planned: [
        { subject: "APP-1", title: "Show the review card", lane: "lane-2", stage: "review", files: ["render.py"] },
        { subject: "APP-2", title: "Say what a story waits on", lane: "lane-1", stage: "planned", after: ["APP-1"] },
      ],
      finish: [], followups: [], leases: [], extensions: {},
    },
    journal: [
      { id: 1, at: "2026-11-17T11:00:00Z", kind: "review.round", lane: "lane-2", subject: "APP-1", data: { round: 1 } },
      { id: 2, at: "2026-11-17T11:30:00Z", kind: "review.round", lane: "lane-2", subject: "APP-1", data: { round: 2 } },
    ],
    publication: {},
  };
}
/** The same record once APP-1 has landed, as the live server sends it: no wait is left. */
function landed() {
  const document = record();
  const [one] = document.state.planned.splice(0, 1);
  document.state.queue.push({ order: 1, subject: "APP-1", title: one.title, lane: "lane-2", stage: "merged", head: "b".repeat(40),
    syncedHead: null, leadInputs: [], reportedRounds: 2, runId: null, checks: [], files: one.files });
  document.state.lanes[1].state = "idle";
  return { ...document, waits: [], waitedOn: [] };
}

const ORDER = "Waits for APP-1 to land. It is in review, round 2.";
const LANE = "Lane 1 is next on APP-2, which waits for APP-1 to land.";
const HOLDING = "Waited on by APP-2.";
const stop = (app, key) => app.querySelector(`:is(.stop-btn, .mg-tick)[data-subject="${key}"]`);
const leftRow = (app, key) => [...app.querySelectorAll(".left-groups li")].find((row) => row.querySelector(`[data-ticket="${key}"]`));
const person = (app, id) => [...app.querySelectorAll(".person")].find((row) => row.querySelector("h3").textContent === (id === "lane-1" ? "Waits" : "Renderer"));

test("a waiting stop says what it waits on, where it is shown, and the story it names is linked", async () => {
  const [, picked, holder] = await openPage(pageWithRuns(record(), {}, { config: CONFIG }), { now: NOW, steps: [
    { click: ':is(.stop-btn, .mg-tick)[data-subject="APP-2"]' }, { click: ':is(.stop-btn, .mg-tick)[data-subject="APP-1"]' }] });
  expect(stop(picked.app, "APP-2").getAttribute("aria-label")).toBe(`Say what a story waits on, Not started. ${ORDER}`);
  const [wait] = picked.app.querySelectorAll("#detail .d-wait");
  expect(wait.textContent).toBe(ORDER);
  expect(wait.querySelector(".issue-link").getAttribute("data-ticket")).toBe("APP-1");
  expect(leftRow(picked.app, "APP-2").querySelector(".left-wait").textContent).toBe(ORDER);
  // An idle lane's wait on its next story is drawn as that story's blocker, not said again in a sentence.
  expect([...person(picked.app, "lane-1").querySelectorAll(".wait")]).toHaveLength(0);
  expect([...person(picked.app, "lane-1").querySelectorAll(".next .dep")].map((pill) => pill.textContent)).toEqual(["APP-1reviewing"]);
  expect([...person(picked.app, "lane-2").querySelectorAll(".wait")]).toHaveLength(0);
  // On the thing that holds: who waits on it, on its stop's label, in its panel and in its row.
  expect([...holder.app.querySelectorAll("#detail .d-wait")].map((line) => line.textContent)).toEqual([HOLDING]);
  expect(stop(holder.app, "APP-1").getAttribute("aria-label").endsWith(HOLDING)).toBe(true);
  expect(leftRow(holder.app, "APP-1").querySelector(".left-wait").textContent).toBe(HOLDING);
});

test("once what frees it happens, every wait is gone with no reload", async () => {
  const snaps = await openPage(pageWithRuns(record(), {}, { config: CONFIG }), { now: NOW, steps: [
    { click: ':is(.stop-btn, .mg-tick)[data-subject="APP-2"]' }, { push: landed() }] });
  expect(snaps[1].text.includes(ORDER)).toBe(true);
  const after = snaps[2];
  for (const words of [ORDER, LANE, HOLDING]) { expect(after.text.includes(words)).toBe(false); }
  expect(after.app.querySelectorAll(".d-wait, .left-wait, .wait")).toHaveLength(0);
  expect(stop(after.app, "APP-2").getAttribute("aria-label")).toBe("Say what a story waits on, Not started");
});

test("the page prints the sentences it is given, each with its own marker, and words none of its own", async () => {
  // Marked as the marker test marks the table: a sentence the page built itself would carry no marker.
  const marked = { ...record(),
    waits: [
      { subject: "APP-2", on: "APP-1", cause: "order", stage: "review", frees: "landed", needsSomeone: false, words: "⟦waits.order⟧ APP-1 then" },
      { subject: "lane-1", on: "APP-2", cause: "order", stage: "planned", frees: "landed", needsSomeone: false, words: "⟦lanes.order⟧ APP-2 next" },
    ],
    waitedOn: [{ subject: "APP-1", by: ["APP-2"], words: "⟦holding.story⟧ by APP-2" }] };
  const [, picked] = await openPage(pageWithRuns(marked, {}, { config: CONFIG }), { now: NOW, steps: [{ click: ':is(.stop-btn, .mg-tick)[data-subject="APP-2"]' }] });
  const lines = [...picked.app.querySelectorAll(".d-wait, .left-wait, .wait")];
  expect(lines.length).toBe(4);
  for (const line of lines) { expect(/^⟦[a-z]+\.[a-z]+⟧ /i.test(line.textContent)).toBe(true); }
  for (const words of ["Waits for", "is next on", "Waited on by", "Held by the lead"]) { expect(picked.text.includes(words)).toBe(false); }
  expect(stop(picked.app, "APP-2").getAttribute("aria-label")).toBe("Say what a story waits on, Not started. ⟦waits.order⟧ APP-1 then");
});

test("the built-in page says the same sentences under the story and under the lane", () => {
  const dom = new JSDOM(builtinPage(record()), { runScripts: "dangerously" });
  const waits = [...dom.window.document.querySelectorAll("#app .wait")].map((line) => line.textContent);
  dom.window.close();
  expect(waits).toEqual([HOLDING, ORDER, LANE]);
  const freed = new JSDOM(builtinPage(landed()), { runScripts: "dangerously" });
  expect(freed.window.document.querySelectorAll("#app .wait")).toHaveLength(0);
  freed.window.close();
});

test("a lane waiting on another story is drawn as progress, with what it waits for, and isn't blocked", async () => {
  const document = record();
  document.state.lanes[0] = { ...lane("lane-1", "waiting"), waitingOn: "APP-1", waitingFrom: { state: "working", at: "2026-11-17T11:00:00Z" } };
  const [first] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW });
  const chip = person(first.app, "lane-1").querySelector(".chip");
  expect(chip.textContent).toBe("Waiting");
  expect(chip.classList.contains("waiting")).toBe(true);
  expect(chip.classList.contains("blocked")).toBe(false);
  expect([...person(first.app, "lane-1").querySelectorAll(".wait")].map((line) => line.textContent))
    .toEqual(["Lane 1 waits for APP-1 to land before it finishes APP-2."]);
});

test("each lane keeps its id's colour wherever the record lists it", async () => {
  const drawn = async (document) => {
    const [first] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW });
    const rows = [...first.app.querySelectorAll(".person")].map((row) => [row.querySelector("h3").textContent, row.style.getPropertyValue("--c")]);
    const lines = [...first.app.querySelectorAll(".line")].map((line) => [line.querySelector(".name b").textContent, line.style.getPropertyValue("--c")]);
    return { rows, lines };
  };
  const inOrder = await drawn(record());
  expect(inOrder.rows).toEqual([["Waits", "var(--l1)"], ["Renderer", "var(--l2)"]]);
  const swapped = record();
  swapped.state.lanes.reverse();
  // The rows follow the record's order; a lane's colour follows its id.
  const moved = await drawn(swapped);
  expect(moved.rows).toEqual([["Renderer", "var(--l2)"], ["Waits", "var(--l1)"]]);
  expect(new Map(moved.lines)).toEqual(new Map(inOrder.lines));
});

test("lanes named other than lane-N get distinct colours that follow their ids", async () => {
  const named = (document) => {
    const renamed = JSON.parse(JSON.stringify(document).replaceAll("lane-1", "api").replaceAll("lane-2", "web"));
    return renamed;
  };
  const colours = async (document) => {
    const [first] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW });
    return new Map([...first.app.querySelectorAll(".person")].map((row) => [row.querySelector("h3").textContent, row.style.getPropertyValue("--c")]));
  };
  const inOrder = await colours(named(record()));
  expect(inOrder.get("Waits") !== inOrder.get("Renderer")).toBe(true);
  const swapped = named(record());
  swapped.state.lanes.reverse();
  expect(await colours(swapped)).toEqual(inOrder);
});

