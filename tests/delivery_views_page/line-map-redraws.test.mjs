// A redraw draws only what changed: a push or tick draws the line map once, the runs refresh that follows draws again
// only when one of the seven blocks it reads changed, What happened eases its height only when the reader changed its
// depth or filter, and a redraw of an unchanged record replays no entrance.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, expect, openPage, pageWithRuns, readJson, served } from "./support.mjs";
import { item, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-22T05:10:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
function corpus() {
  const source = path.join(CORPUS, "source");
  const lines = readFileSync(path.join(source, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(source, "state.json")), journal: lines, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}
const PAGE = pageWithRuns(corpus(), {}, { config: CONFIG });
const LIVE = served(PAGE);
const BLOCKS = ["runs", "links", "reviews", "needs", "files", "left-open", "integration"];

/** The served page with one block changed: its words given one more space, or, where the page has none, one added. */
function changed(page, name) {
  const pattern = new RegExp(`(<script type="application/json" id="delivery-${name}">)([^<]*)(</script>)`);
  if (pattern.test(page)) return page.replace(pattern, (_, open, body, close) => `${open}${body} ${close}`);
  return page.replace("</body>", `<script type="application/json" id="delivery-${name}">{}</script></body>`);
}

/** How many times the page drew itself after it opened: a push, then the runs refresh answering with `answer`. */
async function draws(answer) {
  const reasons = [];
  await openPage(LIVE, { now: NOW, store: undefined, server: { document: corpus() }, rendered: answer, quicker: 1, steps: [
    // A first push turns the connection live, which redraws once for its status; the push measured is the next one.
    { wait: 20 }, { event: corpus() },
    { run(window) {
      const redraw = window.page.redraw;
      window.page.redraw = (draw, why) => { reasons.push(why); return redraw(draw, why); };
    } },
    { event: corpus() },
    { runs: '{"generation": 1}' }, { asked: 1 }, { wait: 30 },
  ] });
  return reasons;
}

test("a push draws the line map once, and the runs refresh that follows draws again only when a block it reads changed", async () => {
  expect(await draws(LIVE)).toEqual(["push"]);
  for (const name of BLOCKS) {
    expect([name, await draws(changed(LIVE, name))]).toEqual([name, ["push", "runs"]]);
  }
});

/** A story told in one chapter, with a landing. */
function storied(beats) {
  const document = record({ queue: [item("APP-1", "merged"), item("APP-2", "queued")] });
  const chapter = { id: "c1", form: "landing", subject: null, pieceTitle: null, at: "2026-10-22T04:00:00Z", entries: [], beats: beats.map(([id]) => id),
    setbacks: [], standing: [], notLanded: [], words: { head: "APP-1 landed", line: "", standing: [] } };
  // A beat is known by the entries it tells, so each tells its own.
  const beat = (id, head, at) => ({ id, form: "entry", tone: "landed", altitudes: ["headline", "story", "everything"], entries: [`${id}-entry`], at,
    words: { head, line: "" } });
  document.story = { schemaVersion: "story/1", state: "open", tones: [], beats: beats.map((args) => beat(...args)), threads: [],
    digest: { landed: 1, pieces: 2, decisions: 0, setbacks: { closed: 0, cost: 0, standing: [] }, words: { head: "One of two.", line: "" } },
    chapters: [chapter] };
  return document;
}
const ONE = [["b0", "APP-1 landed.", "2026-10-22T04:00:00Z"]];
const TWO = [...ONE, ["b1", "APP-2 landed.", "2026-10-22T04:30:00Z"]];

test("What happened eases its height only when the reader changes its depth or filter, never on a tick", async () => {
  // Two ticks, then a push that adds a beat, so What happened grows: none of them is the reader's, so none eases.
  const opened = await openPage(pageWithRuns(storied(ONE), {}, { config: CONFIG }), { now: NOW, store: {}, motion: {}, steps: [
    { tick: true }, { tick: true }, { push: storied(TWO) },
    { click: '#happened .story-bar button[data-tab="decisions"]' },
  ] });
  const eases = (animations) => animations.filter((animation) => animation.options?.id === "run-height" && animation.target === "inner");
  // What happened's own ease comes from the reader's switch, once: here a filter that hides the landing.
  expect(eases(opened.animations).length).toBe(1);
});

const ENTRANCES = ".fresh, .arriving, .landing, .grew, .arrive";
test("a redraw of an unchanged record replays no entrance, and a new beat still lands fresh", async () => {
  const marked = [];
  await openPage(pageWithRuns(storied(ONE), {}, { config: CONFIG }), { now: NOW, store: {}, motion: {}, steps: [
    { tick: true }, { run(window) { marked.push(window.document.querySelectorAll(`#app :is(${ENTRANCES})`).length); } },
    { tick: true }, { run(window) { marked.push(window.document.querySelectorAll(`#app :is(${ENTRANCES})`).length); } },
    { push: storied(TWO) }, { run(window) { marked.push([...window.document.querySelectorAll("#happened .beat.fresh")].map((beat) => beat.getAttribute("data-beat"))); } },
  ] });
  expect(marked).toEqual([0, 0, ["b1"]]);
});
