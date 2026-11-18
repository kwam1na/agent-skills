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

test("the stylesheet draws the line only on a thread's span, the hairline once closed, and never joins across a closing", () => {
  const stylesheet = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "line-map.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = stylesheet.match(/[^{}]*\{[^}]*\}/g).map((block) => block.trim());
  const selectors = (block) => block.split("{")[0].split(",").map((each) => each.trim());
  const drawing = rules.filter((block) => /content:\s*""/.test(block) && selectors(block).some((one) => /\.beat\.threaded.*::before$/.test(one)));
  // Every rule that draws a threaded row's line names the span: no rule draws it on a row outside one.
  expect(drawing.length > 0).toBe(true);
  for (const block of drawing) expect(selectors(block).every((one) => one.includes(".t-span"))).toBe(true);
  expect(rules.some((block) => selectors(block).includes(".beat.threaded.t-span.t-closed::before") && /background:\s*var\(--hair\)/.test(block))).toBe(true);
  const joins = rules.filter((block) => /top:\s*-1px/.test(block) && /\.beat\.threaded/.test(block));
  expect(joins.length).toBe(1);
  expect(joins[0].includes(":not(.t-closes)")).toBe(true);
});

test("nothing on a thread moves, under reduced motion or not", async () => {
  for (const reduced of [true, false]) {
    const snapshots = await openPage(pageWithRuns(storied("thread-at-story"), {}, { config: CONFIG }),
      { now: "2026-11-16T12:00:00Z", store: false, motion: { reduced }, steps: [{ click: '#happened [data-depth="story"]' }] });
    expect(snapshots[1].app.querySelectorAll("#happened .thread-ring").length).toBe(1);
    expect(snapshots.animations.filter((animation) => /thread-|beat/.test(String(animation.target)))).toEqual([]);
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
function checking(at, gap = 500) {
  // "failed" is the first result, then the end-to-end run failing with nothing after it. `gap` is how long after the
  // storefront build the end-to-end run finishes, in milliseconds.
  const stage = at === "failed" || at === "failed-running" ? 1 : at === "smoke" ? 2 : at;
  const second = new Date(Date.parse("2026-10-22T05:09:30Z") + gap).toISOString();
  const state = readJson(path.join(CORPUS, "source", "state.json"));
  const journal = readFileSync(path.join(CORPUS, "source", "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  const steps = state.meta.checking.now.steps;
  const step = (label) => steps.find((each) => each.label === label);
  if (stage >= 1) {
    Object.assign(step("storefront build"), { state: "passed", finishedAt: "2026-10-22T05:09:30Z" });
    Object.assign(step("checkout end-to-end"), { state: "running", startedAt: "2026-10-22T05:09:30Z" });
  }
  if (stage >= 2) {
    Object.assign(step("checkout end-to-end"), { state: "passed", finishedAt: second });
    Object.assign(step("payments behavior scenarios"), { state: "running", startedAt: second });
  }
  if (stage === 3) {
    Object.assign(step("payments behavior scenarios"), { state: "passed", finishedAt: "2026-10-22T05:09:30.600Z" });
  }
  if (at === "smoke") {
    Object.assign(step("payments behavior scenarios"), { state: "passed", finishedAt: "2026-10-22T05:09:30.600Z" });
    steps.push({ label: "smoke test", state: "running", startedAt: "2026-10-22T05:09:30.600Z" });
  }
  if (at === "failed" || at === "failed-running") {
    Object.assign(step("checkout end-to-end"), { state: "failed", finishedAt: "2026-10-22T05:09:30.500Z" });
  }
  if (at === "failed-running") {
    Object.assign(step("payments behavior scenarios"), { state: "running", startedAt: "2026-10-22T05:09:30.500Z" });
  }
  return { state, journal, focus: {}, stats: {}, estimates: {} };
}
const runRows = (shown) => [...shown.app.querySelectorAll(".checking .ck-run")].map((row) => ({
  classes: row.className, name: row.querySelector(".ck-run-name")?.textContent ?? row.querySelector(".ck-run-words").textContent }));
const turnsOut = (animations) => animations.filter((animation) => animation.target === "ck-run-words"
  && JSON.stringify(animation.keyframes).includes("rotateX(70deg)")).map((animation) => Math.round(animation.options.duration));
const turnsIn = (animations) => animations.filter((animation) => animation.target === "ck-run-words"
  && JSON.stringify(animation.keyframes).includes("rotateX(-70deg)")).map((animation) => Math.round(animation.options.duration));

test("a redraw mid-handoff keeps the turning row, the next result waits its turn, and a quicker result turns quicker", async () => {
  // Timers run four times faster in these cases: a whole handoff is under half a second, and each push lands well inside it.
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  // At the page's own pace, so the settle and the hold can be timed: the first handoff takes its whole 1.88 s, the
  // second about half a second. Read 3.2 s after the first result, the row has turned twice; had the second handoff
  // taken its whole length it would still be turning until about 3.8 s.
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {},
    steps: [{ push: checking(1) }, { push: checking(2) }, { wait: 3150 }] });
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
  expect(turnsIn(snapshots.animations)).toEqual([300, Math.round(300 * 500 / 1880)]);
});

test("a quicker result's whole handoff is shortened, its settle and hold with its turn", async () => {
  // The second result came 100 ms after the first, so its handoff is a quarter of the whole: 470 ms in all, where the
  // whole is 1880 ms. Timed from the row showing the end-to-end run to its showing the behavior scenarios, measured on
  // the test's own clock, at the page's own pace.
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const marks = [];
  const polls = Array.from({ length: 45 }, () => ({ run: () => marks.push(performance.now()), wait: 80 }));
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {},
    steps: [{ push: checking(1) }, { push: checking(2, 100) }, ...polls] });
  const rows = snapshots.slice(3).map(runRows);
  // The second handoff begins when the row first names the end-to-end run, and ends when it shows the scenarios running.
  const second = rows.findIndex((shown) => shown.some((row) => row.name.includes("checkout end-to-end")));
  const third = rows.findIndex((shown) => shown.some((row) => row.classes === "ck-run" && row.name === "payments behavior scenarios"));
  expect([second >= 0, third > second]).toEqual([true, true]);
  // About 470 ms, well under the 830 ms it takes with an unshortened settle, or the 1145 ms with an unshortened hold.
  const took = marks[third] - marks[second];
  expect([took < 650, took]).toEqual([true, took]);
});

test("a quicker result's handoff is a quarter of the whole at the least, and one that took longer is the whole", async () => {
  const turned = async (gap) => {
    const page = pageWithRuns(checking(0), {}, { config: CONFIG });
    const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {}, quicker: 4,
      steps: [{ push: checking(1) }, { push: checking(2, gap) }, { wait: 1500 }] });
    return turnsOut(snapshots.animations);
  };
  expect(await turned(100)).toEqual([200, 50]);
  expect(await turned(5000)).toEqual([200, 200]);
});

test("a new run mid-handoff drops the turning row and shows the new run's running check", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const next = checking(0);
  next.state.meta.checking.now.startedAt = "2026-10-22T05:09:40Z";
  next.state.meta.checking.now.steps.forEach((step) => {
    if (step.label === "storefront build") Object.assign(step, { startedAt: "2026-10-22T05:09:40Z" });
  });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {}, quicker: 4,
    steps: [{ push: checking(1) }, { push: next }, { wait: 1500 }] });
  expect(runRows(snapshots[2])).toEqual([{ classes: "ck-run", name: "storefront build" }]);
  expect(runRows(snapshots[3])).toEqual([{ classes: "ck-run", name: "storefront build" }]);
});

test("under reduced motion a finished check simply gives way to the next, with nothing turning", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: { reduced: true },
    steps: [{ push: checking(1) }, { push: checking(2) }] });
  expect(runRows(snapshots[1])).toEqual([{ classes: "ck-run", name: "checkout end-to-end" }]);
  expect(runRows(snapshots[2])).toEqual([{ classes: "ck-run", name: "payments behavior scenarios" }]);
  expect(turnsOut(snapshots.animations)).toEqual([]);
});

test("results a moment apart don't pile up: only the latest waits, so the row turns once more and rests on the record", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  // At the page's own pace, so all three pushes land inside the first handoff.
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {},
    steps: [{ push: checking(1) }, { push: checking(2) }, { push: checking(3) }, { wait: 3000 }] });
  // The first result turned the row to the end-to-end run; the next two came while it turned, and only the last of
  // them, the behavior scenarios passing with nothing after, played: it settles on its tick, with no second turn.
  expect(turnsOut(snapshots.animations)).toEqual([200]);
  expect(runRows(snapshots[4])).toEqual([{ classes: "ck-run settled is-passed landing", name: "payments behavior scenarios passed" }]);
});

test("a check that fails while the row is turning is told by its entry alone, arriving at once, with no second telling queued", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {}, quicker: 4,
    steps: [{ push: checking(1) }, { push: checking("failed") }, { wait: 1500 }] });
  const [, , failedNow, after] = snapshots;
  const entry = failedNow.app.querySelector('.ck-fails li[data-check="checkout end-to-end"]');
  expect(entry.classList.contains("arriving")).toBe(false);
  // The storefront build's turn plays out on its kept row, and nothing turns after it.
  expect(runRows(failedNow).length).toBe(1);
  expect(turnsOut(snapshots.animations)).toEqual([200]);
  expect(after.app.querySelectorAll(".checking .ck-run.is-failed").length).toBe(0);
});

test("a check that fails while the row is turning, with another still running, has its entry arrive at once, not on a delay the kept row may not keep", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {},
    steps: [{ push: checking(1) }, { push: checking("failed-running") }] });
  const entry = snapshots[2].app.querySelector('.ck-fails li[data-check="checkout end-to-end"]');
  expect(entry.classList.contains("arriving")).toBe(false);
  expect(runRows(snapshots[2]).length).toBe(1);
  expect(runRows(snapshots[2])[0].name).toContain("storefront build");
});

test("two results while the row turns, each with a check after it: only the latest turns next, straight to the newest check", async () => {
  const page = pageWithRuns(checking(0), {}, { config: CONFIG });
  const snapshots = await openPage(page, { now: "2026-10-22T05:10:00Z", store: {}, motion: {},
    steps: [{ push: checking(1) }, { push: checking(2) }, { push: checking("smoke") }, { wait: 3000 }] });
  // The end-to-end result was overtaken while it waited: the row turns from the storefront build to the end-to-end run,
  // then once more, straight to the smoke test, never stopping on the scenarios.
  expect(turnsOut(snapshots.animations).length).toBe(2);
  expect(runRows(snapshots[4])).toEqual([{ classes: "ck-run", name: "smoke test" }]);
});

