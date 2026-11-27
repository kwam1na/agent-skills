// The page controller, page.js: every delivery view loads it first, and every asset asks its one motion check. Reduced
// motion stills everything; a key press stills only the motion the reader's own action starts, so a keyboard reader
// still sees a push land.
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { JSDOM } from "jsdom";

import { CORPUS, REPLAYED_RUNS, ROOT, RUNS_CORPUS, expect, openPage, pageWithRuns, readJson, renderHistoryPage, renderRunsPage } from "./support.mjs";
import { finishLine, item, journal, planFor, record, unit } from "./plan-fixtures.mjs";

process.env.TZ = "UTC";
delete process.env.DELIVERY_PLAN_PREVIEWS;

const ASSETS = path.join(ROOT, "skills", "delivery-views", "assets");
const asset = (name) => readFileSync(path.join(ASSETS, name), "utf8");
// Each asset's first line, as it reaches the page.
const BANNER = { page: asset("page.js").split("\n")[0], summary: asset("run-summary.js").split("\n")[0] };

/** Where the page controller's and the run summary's banners sit in a rendered page; -1 when one is missing. */
function order(rendered) {
  return { page: rendered.indexOf(BANNER.page), summary: rendered.indexOf(BANNER.summary) };
}
const loadedFirst = ({ page, summary }) => page !== -1 && summary !== -1 && page < summary;
/** The line-map corpus's own record, as the motion tests read it. */
function corpusRecord() {
  const source = path.join(CORPUS, "source");
  const lines = readFileSync(path.join(source, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(source, "state.json")), journal: lines, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}

test("every view loads the page controller before the run summary", () => {
  expect(BANNER.page.startsWith("// The delivery views' page controller")).toBe(true);
  const lineMap = pageWithRuns(corpusRecord(), {}, { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS } });
  const runs = renderRunsPage({ spec: "delivery-state-absent/1" }, { runsCommands: readJson(path.join(RUNS_CORPUS, "runs-commands.json")) });
  const history = renderHistoryPage({}, { config: { spec: "delivery-views-config/1" }, now: "2026-10-22T12:00:00Z" });
  for (const [name, rendered] of Object.entries({ lineMap, runs, history })) {
    expect([name, loadedFirst(order(rendered))]).toEqual([name, true]);
  }
  // The check fails a page that leaves the controller out.
  expect(loadedFirst(order(runs.replace(asset("page.js"), "")))).toBe(false);
});

/** Twelve pieces in one lane with the first `count` landed, as the flip tests draw them. */
const NOW = "2026-10-30T12:00:00Z";
const SUBJECTS = Array.from({ length: 12 }, (_, index) => `P${index + 1}`);
const landed = (subject, at) => [[at, "approve", subject], [at + 1, "candidate.handed", subject], [at + 2, "candidate.merged", subject]];
function delivery(count) {
  return record({
    queue: SUBJECTS.map((subject, index) => item(subject, index < count ? "merged" : "queued")),
    journal: journal(SUBJECTS.slice(0, count).flatMap((subject, index) => landed(subject, 10 + index * 5))),
    finish: [{ id: "all", text: "Every piece is proven", met: false }],
  });
}
const PLAN = planFor(SUBJECTS.map((subject) => unit(subject)), { finish: [finishLine("all", { criteria: "all", proofs: [] })] });
const LANDING = pageWithRuns(delivery(9), {}, { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS }, plan: PLAN });
const key = { run(window) { window.document.body.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Shift", bubbles: true })); } };
const pointer = { run(window) { window.document.body.dispatchEvent(new window.Event("pointerdown", { bubbles: true })); } };

test("after a key press a push still lands its story and flips its counts; under reduced motion it does not", async () => {
  const lands = async (motion) => {
    const [, , pushed] = await openPage(LANDING, { now: NOW, store: {}, motion, steps: [key, { push: delivery(10) }] });
    return { landing: pushed.app.querySelectorAll(".mg-tick.landing").length, flips: pushed.app.querySelectorAll(".flip").length };
  };
  const keyboard = await lands({});
  expect(keyboard.landing).toBe(1);
  expect(keyboard.flips > 0).toBe(true);
  expect(await lands({ reduced: true })).toEqual({ landing: 0, flips: 0 });
});

test("after a key press a fold opens without its ease; after a pointer press it eases again", async () => {
  const page = pageWithRuns(corpusRecord(), {}, { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS } });
  const summary = { click: "#detail details.mh summary" };
  const isOpen = (snapshot) => snapshot.app.querySelector("#detail details.mh")?.hasAttribute("open");
  const slides = (opened) => opened.animations.filter((animation) => animation.target === "mh" && animation.options?.id === "run-height").length;
  // By key: the merge check's steps open and close at once.
  const byKey = await openPage(page, { now: "2026-10-22T05:10:00Z", motion: {}, steps: [{ click: '[data-subject="PAY-2358"]' }, key, summary, summary] });
  expect(byKey.slice(3).map(isOpen)).toEqual([true, false]);
  expect(slides(byKey)).toBe(0);
  // A pointer press after the key press: the next close and open each ease.
  const thenPointer = await openPage(page, { now: "2026-10-22T05:10:00Z", motion: {}, steps: [{ click: '[data-subject="PAY-2358"]' }, key, summary, pointer, summary, summary] });
  expect(thenPointer.slice(3).map(isOpen)).toEqual([true, true, false, true]);
  expect(slides(thenPointer)).toBe(2);
});

/** The `.js` assets that name the reduced-motion query, in either quote style: the controller's check must be the one. */
function motionChecks(sources) {
  return Object.entries(sources).filter(([, source]) => /prefers-reduced-motion/.test(source)).map(([name]) => name).sort();
}

test("page.js holds the only reduced-motion check among the views' scripts", () => {
  const sources = Object.fromEntries(readdirSync(ASSETS).filter((name) => name.endsWith(".js")).map((name) => [name, asset(name)]));
  for (const name of ["history.js", "line-map.js", "page.js", "run-summary.js", "runs.js"]) expect(Object.keys(sources)).toContain(name);
  expect(motionChecks(sources)).toEqual(["page.js"]);
  // A copy planted in another asset, in either quote style, is found: ask page.motion instead.
  for (const planted of ["matchMedia(\"(prefers-reduced-motion: reduce)\")", "matchMedia('(prefers-reduced-motion: reduce)')"]) {
    expect(motionChecks({ ...sources, "runs.js": `${sources["runs.js"]}\n${planted};` })).toEqual(["page.js", "runs.js"]);
  }
});

test("page.motion refuses reduced motion always and a key press only for the reader's own motion; page.key names kind and id", () => {
  let reduced = false;
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", beforeParse(window) {
    window.matchMedia = (query) => ({ matches: query.includes("prefers-reduced-motion: reduce") && reduced });
  } });
  const { window } = dom;
  window.eval(asset("page.js"));
  const asked = () => [window.page.motion(true), window.page.motion(false)];
  expect(asked()).toEqual([true, true]);
  window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab" }));
  expect(asked()).toEqual([false, true]);
  window.document.dispatchEvent(new window.Event("pointerdown"));
  expect(asked()).toEqual([true, true]);
  reduced = true;
  expect(asked()).toEqual([false, false]);
  expect(Object.keys(window.page).sort()).toEqual(["key", "motion"]);
  expect(window.page.key("stop", "PAY-2358")).toBe("stop:PAY-2358");
  expect(window.page.key("decision", 3)).toBe("decision:3");
  for (const [kind, id] of [["", "x"], [5, "x"], [{}, "x"], ["stop", ""], ["stop", undefined], ["stop", null]]) {
    expect((() => { try { window.page.key(kind, id); return null; } catch (error) { return error.name; } })()).toBe("TypeError");
  }
  dom.window.close();
});
