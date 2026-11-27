// A redraw keeps the reader's place. The page redraws itself on the 30-second tick, on a new width and on every pushed
// record, and each goes through the page controller's one redraw transaction (page.redraw): it notes the element at the
// top of the window and its offset, draws, and puts that element back where it was, or, when the element is gone, the
// same scroll clamped to the new height. Turning to another page still lands at its top, or its end.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, expect, openPage, pageWithRuns, readJson } from "./support.mjs";

const NOW = "2026-10-22T05:10:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
function record() {
  const source = path.join(CORPUS, "source");
  const journal = readFileSync(path.join(source, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(source, "state.json")), journal, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}
const PAGE = pageWithRuns(record(), {}, { config: CONFIG });

/** Steps that turn to a chapter by its address, as an outside link would. */
const turnTo = (id) => ({ run(window) { window.location.hash = "#" + id; window.dispatchEvent(new window.HashChangeEvent("hashchange")); } });
/** A step that records where the window is, and what the page holds, into `seen`. */
const note = (seen, label) => ({ run(window) {
  seen.push([label, window.scrollY, window.document.documentElement.scrollHeight - window.innerHeight]);
} });
/** Scrolls until the line chapter's end meets the window's bottom. */
const toEnd = { run(window) {
  const end = window.document.getElementById("line").getBoundingClientRect().bottom;
  window.scrollTo({ top: window.scrollY + end - window.innerHeight, behavior: "instant" });
} };
const resize = { run(window) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: window.innerWidth + 200 });
  window.dispatchEvent(new window.Event("resize"));
}, wait: 260 };

async function place(steps, scenario = {}) {
  const seen = [];
  await openPage(PAGE, { now: NOW, store: {}, layout: {}, ...scenario, steps: steps(seen) });
  return seen;
}

for (const [layout, stored] of [["paged", {}], ["one-page", { "delivery-views:test:layout": "scroll" }]]) {
  test(`${layout}: scrolled to the end of the line, a tick, a new width and a push leave the window where it was`, async () => {
    const seen = await place((seen) => [turnTo("line"), toEnd, note(seen, "before"),
      { tick: true }, note(seen, "tick"), resize, note(seen, "resize"), { push: record() }, note(seen, "push")],
    { rendered: PAGE, stored });
    const before = seen[0][1];
    expect(before > 0).toBe(true);
    expect(seen.map(([label, y]) => [label, y])).toEqual([["before", before], ["tick", before], ["resize", before], ["push", before]]);
  });
}

/** The record with more stories planned, which draws more above the chapters that follow Now. */
function grown(count) {
  const next = record();
  for (let index = 0; index < count; index += 1) {
    next.state.planned.push({ subject: `PAY-29${index}0`, title: `More work ${index}`, lane: "backend", stage: "planned" });
  }
  return next;
}
const topOf = (window, id) => window.document.getElementById(id).getBoundingClientRect().top;

test("one-page: what grows above the reader on a push leaves the chapter they read where it stood in the window", async () => {
  const seen = [];
  await openPage(PAGE, { now: NOW, store: {}, layout: {}, rendered: PAGE, stored: { "delivery-views:test:layout": "scroll" }, steps: [
    { run(window) { window.scrollTo({ top: window.scrollY + topOf(window, "left") - 40, behavior: "instant" }); } },
    { run(window) { seen.push([topOf(window, "left"), window.scrollY]); } },
    { push: grown(6) },
    { run(window) { seen.push([topOf(window, "left"), window.scrollY]); } },
  ] });
  const [[before, y], [after, moved]] = seen;
  expect(before).toBe(40);
  expect(after).toBe(40);
  // The page grew above the reader, so the window followed it.
  expect(moved > y).toBe(true);
});

test("paged: turning to another chapter still lands at its top, and turning back up past one lands at its end", async () => {
  const seen = [];
  const touch = (type, y) => ({ run(window) {
    const event = new window.Event(type);
    Object.defineProperty(event, "touches", { value: y === null ? [] : [{ clientY: y }] });
    window.dispatchEvent(event);
  } });
  await openPage(PAGE, { now: NOW, store: {}, layout: {}, steps: [
    turnTo("line"), toEnd, note(seen, "line end"),
    turnTo("left"), note(seen, "left"),
    // A pull down from the top of What's left turns back to the line, landing at its end.
    touch("touchstart", 100), touch("touchmove", 400), touch("touchend", null), { wait: 20 }, note(seen, "back"),
    { tick: true }, note(seen, "tick"),
  ] });
  const [[, lineEnd], [, left], [, back, backMost], [, tick]] = seen;
  expect(lineEnd > 0).toBe(true);
  expect(left).toBe(0);
  expect([back, tick]).toEqual([backMost, backMost]);
});

/** One-page, the reader 5px into the element `selector` finds, then a push that plans six stories above it in the line. */
async function intoThenGrown(selector) {
  const seen = [];
  const top = (window) => window.document.querySelector(selector).getBoundingClientRect().top;
  await openPage(PAGE, { now: NOW, store: {}, layout: {}, rendered: PAGE, stored: { "delivery-views:test:layout": "scroll" }, steps: [
    { run(window) { window.scrollTo({ top: window.scrollY + top(window) + 5, behavior: "instant" }); } },
    { run(window) { seen.push([top(window), window.scrollY]); } },
    { push: grown(6) },
    { run(window) { seen.push([top(window), window.scrollY]); } },
  ] });
  return seen;
}

test("one-page: a reader inside the line keeps the very element they read when stories are planned above it", async () => {
  // A tick on the main line, whose story also has a stop: the tick is the element read, not the chapter or the stop.
  const [[before, y], [after, moved]] = await intoThenGrown('#line g.mg-tick[data-subject="PAY-2366"]');
  expect([before, after]).toEqual([-5, -5]);
  expect(moved > y).toBe(true);
});

test("a new connection status and a record read draw at once, even within a frame another redraw took", async () => {
  const headline = (text) => { const next = record(); next.state.meta = { ...next.state.meta, headline: text }; return next; };
  const words = (shot) => shot.app.querySelector(".fact.since").textContent;
  const [, down, live] = await openPage(PAGE, { now: NOW, frames: true, store: { read: "pending" }, steps: [{ status: false }, { status: true }] });
  expect(words(down)).toContain("Reconnecting");
  expect(words(live)).toContain("Live");
  const [, read] = await openPage(PAGE, { now: NOW, frames: true, store: { read: headline("Read in the same frame") }, steps: [{ wait: 20 }] });
  expect(read.text).toContain("Read in the same frame");
});
