// The story chapter's setback threads drawn by their state, a beat's long list of checks folded, and the Checking card's
// finishing handoff played to its end across live redraws. The clock and time zone are fixed, and storage starts empty.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, ROOT, expect, openPage, pageWithRuns, readJson, told } from "./support.mjs";
import { item, record } from "./plan-fixtures.mjs";

const CONFIG = {
  spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^[A-Z]$" },
  forges: [{ name: "GitHub", repository: "acme/shop", pullUrl: "https://github.com/{repository}/pull/{number}",
    commitUrl: "https://github.com/{repository}/commit/{sha}" }],
};

/** A record holding a narrator journal, with the story the narrator told of it, kept beside it. */
function storied(name) {
  const folder = path.join(ROOT, "tests", "fixtures", "narrator", name);
  const journal = readFileSync(path.join(folder, "journal.jsonl"), "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const state = readJson(path.join(folder, "state.json"));
  const document = record({ queue: state.queue.map((piece) => item(piece.subject, piece.stage)), journal });
  document.story = readJson(path.join(ROOT, "tests", "fixtures", "delivery-views", "story", `${name}.json`));
  return document;
}
async function atStory(document, scenario = {}) {
  const [, shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG }),
    { now: "2026-11-16T12:00:00Z", store: false, ...scenario, steps: [{ click: '#happened [data-depth="story"]' }] });
  return shown;
}
/** Each threaded row at the story altitude: its beat, and what it carries of its thread. */
const marks = (shown) => [...shown.app.querySelectorAll("#happened .beat.threaded")].map((row) => [row.getAttribute("data-beat"),
  ["t-open", "t-closed", "t-span", "t-opens", "t-closes"].filter((name) => row.classList.contains(name)).join(" "),
  row.querySelectorAll(":scope > .thread-ring").length, row.querySelectorAll(":scope > .thread-tick").length]);

test("a closed thread's line is the hairline from a ring where it opened to a tick where it closed, its cause and aftermath clear of it", async () => {
  // Thread t1 holds entries 4 to 9; it opened at entry 6 (beat b7) and closed at entry 7 (beat b8).
  const shown = await atStory(storied("thread-at-story"));
  // The newest row is drawn first.
  expect(marks(shown)).toEqual([
    ["b10", "t-closed", 0, 0], ["b9", "t-closed", 0, 0],
    ["b8", "t-closed t-span t-closes", 0, 1], ["b7", "t-closed t-span t-opens", 1, 0],
    ["b5", "t-closed", 0, 0], ["b4", "t-closed", 0, 0]]);
});

test("an open thread's line stays held, with its ring and no tick", async () => {
  const shown = await atStory(storied("standing-break"));
  expect(marks(shown)).toEqual([["b5", "t-open t-span t-opens", 1, 0]]);
});

test("a thread's line runs through the rows between its ring and its tick, and an open one on past its ring", async () => {
  // Thread t1 opened at entry 5 (beat b6) and closed at entry 8 (beat b9), with two rows between.
  const closed = await atStory(storied("break-stops-a-piece"));
  expect(marks(closed)).toEqual([
    ["b9", "t-closed t-span t-closes", 0, 1], ["b8", "t-closed t-span", 0, 0],
    ["b7", "t-closed t-span", 0, 0], ["b6", "t-closed t-span t-opens", 1, 0]]);
  // The same setback still standing: no tick, and every row since its opening on the held line.
  const document = storied("break-stops-a-piece");
  Object.assign(document.story.threads[0], { state: "open", closing: null, closedAt: null });
  const open = await atStory(document);
  expect(marks(open)).toEqual([
    ["b9", "t-open t-span", 0, 0], ["b8", "t-open t-span", 0, 0],
    ["b7", "t-open t-span", 0, 0], ["b6", "t-open t-span t-opens", 1, 0]]);
});

test("two setbacks back to back read as two: the first ends on its tick and the next begins on its own ring", async () => {
  const document = storied("thread-at-story");
  const story = document.story;
  const [first] = story.threads;
  // t1 now ends at entry 7, and a second setback opens at entry 8 and closes at entry 9, on the rows that follow it.
  first.entries = [4, 5, 6, 7];
  story.threads.push({ ...structuredClone(first), id: "t2", opening: 8, closing: 9, entries: [8, 9] });
  story.beats.filter((beat) => beat.id === "b9" || beat.id === "b10").forEach((beat) => { beat.thread = "t2"; });
  const shown = await atStory(document);
  expect(marks(shown)).toEqual([
    ["b10", "t-closed t-span t-closes", 0, 1], ["b9", "t-closed t-span t-opens", 1, 0],
    ["b8", "t-closed t-span t-closes", 0, 1], ["b7", "t-closed t-span t-opens", 1, 0],
    ["b5", "t-closed", 0, 0], ["b4", "t-closed", 0, 0]]);
  expect([...shown.app.querySelectorAll("#happened .beat.threaded")].map((row) => row.getAttribute("data-thread"))).toEqual(
    ["t2", "t2", "t1", "t1", "t1", "t1"]);
});

test("nothing on a thread moves, under reduced motion or not", async () => {
  for (const reduced of [true, false]) {
    const [, shown] = await openPage(pageWithRuns(storied("thread-at-story"), {}, { config: CONFIG }),
      { now: "2026-11-16T12:00:00Z", store: false, motion: { reduced }, steps: [{ click: '#happened [data-depth="story"]' }] });
    expect(shown.app.querySelectorAll("#happened .thread-ring").length).toBe(1);
  }
  const stylesheet = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "line-map.css"), "utf8");
  for (const rule of stylesheet.match(/[^{}]*\{[^}]*\}/g).filter((block) => /thread-ring|thread-tick|t-span|t-opens|t-closes/.test(block))) {
    expect([rule.trim().split("{")[0], /animation|transition/.test(rule)]).toEqual([rule.trim().split("{")[0], false]);
  }
});

test("a beat that chose checks counts them in its line and names them only in its fold", async () => {
  const folder = path.join(ROOT, "tests", "fixtures", "narrator", "last-delivery");
  const journal = readFileSync(path.join(folder, "journal.jsonl"), "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const state = readJson(path.join(folder, "state.json"));
  const document = told(record({ queue: state.queue.map((piece) => item(piece.subject, piece.stage)), journal }));
  const chosen = journal.find((entry) => entry.id === 76);
  const beat = document.story.beats.find((each) => each.form === "entry" && each.entries.includes(76));
  expect(beat.words.line).toBe(`${chosen.data.checks.length} checks at ${chosen.data.head.slice(0, 7)}. ${chosen.data.why}`);
  const page = pageWithRuns(document, {}, { config: CONFIG });
  const [, shown, opened] = await openPage(page, { now: "2026-11-16T12:00:00Z", store: false,
    steps: [{ click: '#happened [data-depth="story"]' }, { click: `#happened [data-beat="${beat.id}"] summary` }] });
  const row = shown.app.querySelector(`#happened [data-beat="${beat.id}"]`);
  const fold = row.querySelector("details.beat-fold");
  expect(fold.hasAttribute("open")).toBe(false);
  // The line says how many and why; no check's name is in it.
  expect(fold.querySelector("summary .beat-line").textContent).toBe(beat.words.line);
  for (const name of chosen.data.checks) expect(fold.querySelector("summary").textContent.includes(name)).toBe(false);
  // Opened, the fold names each check, one to a line, as the entry holds them.
  const names = [...opened.app.querySelectorAll(`#happened [data-beat="${beat.id}"] .beat-names li`)].map((li) => li.textContent);
  expect(names).toEqual(chosen.data.checks);
  expect(opened.app.querySelector(`#happened [data-beat="${beat.id}"] details.beat-fold`).hasAttribute("open")).toBe(true);
});

test("a hand-off check counts the lead's notes in its line and lists them only in its fold", async () => {
  // Entry 8 is the lead's check of A's hand-off, with one note; its beat is b9. The narrator tells a record's story when
  // the page is drawn, so an older record's notes are counted and folded the same way.
  const document = storied("thread-at-story");
  const beat = document.story.beats.find((each) => each.id === "b9");
  const note = document.journal.find((entry) => entry.id === 8).data.notes[0];
  expect(beat.words.line).toBe("At e825ec5, after 2 review rounds, with 1 note");
  const [, shown, opened] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: "2026-11-16T12:00:00Z", store: false,
    steps: [{ click: '#happened [data-depth="story"]' }, { click: '#happened [data-beat="b9"] summary' }] });
  const fold = shown.app.querySelector('#happened [data-beat="b9"] details.beat-fold');
  expect(fold.hasAttribute("open")).toBe(false);
  expect(fold.querySelector("summary").textContent.includes(note)).toBe(false);
  expect([...opened.app.querySelectorAll('#happened [data-beat="b9"] .beat-names li')].map((li) => li.textContent)).toEqual([note]);
});

// The Checking card: the storefront build runs, then passes and hands off to the end-to-end run, which passes half a
// second later and hands off to the behavior scenarios.
function checking(stage) {
  const state = readJson(path.join(CORPUS, "source", "state.json"));
  const journal = readFileSync(path.join(CORPUS, "source", "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  const steps = state.meta.checking.now.steps;
  const step = (label) => steps.find((each) => each.label === label);
  if (stage >= 1) {
    Object.assign(step("storefront build"), { state: "passed", finishedAt: "2026-10-22T05:09:30Z" });
    Object.assign(step("checkout end-to-end"), { state: "running", startedAt: "2026-10-22T05:09:30Z" });
  }
  if (stage >= 2) {
    Object.assign(step("checkout end-to-end"), { state: "passed", finishedAt: "2026-10-22T05:09:30.500Z" });
    Object.assign(step("payments behavior scenarios"), { state: "running", startedAt: "2026-10-22T05:09:30.500Z" });
  }
  return { state, journal, focus: {}, stats: {}, estimates: {} };
}
const runRows = (shown) => [...shown.app.querySelectorAll(".checking .ck-run")].map((row) => ({
  classes: row.className, name: row.querySelector(".ck-run-name")?.textContent ?? row.querySelector(".ck-run-words").textContent }));
const turnsOut = (animations) => animations.filter((animation) => animation.target === "ck-run-words"
  && JSON.stringify(animation.keyframes).includes("rotateX(70deg)")).map((animation) => Math.round(animation.options.duration));

test("a redraw mid-handoff keeps the turning row, the next result waits its turn, and a quicker result turns quicker", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {}, quicker: 10,
    steps: [{ push: checking(1) }, { push: checking(2) }, { wait: 600 }] });
  const [, handing, redrawn, rested] = snapshots;
  // The storefront build's row is handing off, and the redraw that brings the next result keeps it as it was: one row,
  // still the storefront build's, not rebuilt on the newest running check.
  expect(runRows(handing).length).toBe(1);
  expect(runRows(handing)[0].name).toContain("storefront build");
  expect(runRows(redrawn).length).toBe(1);
  expect(runRows(redrawn)[0].name).toContain("storefront build");
  // Both results turned, in order, the second in the half second its check took rather than the whole handoff.
  expect(runRows(rested)).toEqual([{ classes: "ck-run", name: "payments behavior scenarios" }]);
  const turns = turnsOut(snapshots.animations);
  expect(turns.length).toBe(2);
  expect(turns[0]).toBe(200);
  expect(turns[1]).toBe(Math.round(200 * 500 / 1880));
});

test("under reduced motion a finished check simply gives way to the next, with nothing turning", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: { reduced: true },
    steps: [{ push: checking(1) }, { push: checking(2) }] });
  expect(runRows(snapshots[1])).toEqual([{ classes: "ck-run", name: "checkout end-to-end" }]);
  expect(runRows(snapshots[2])).toEqual([{ classes: "ck-run", name: "payments behavior scenarios" }]);
  expect(turnsOut(snapshots.animations)).toEqual([]);
});
