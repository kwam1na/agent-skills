// What happened tells each setback once, in one ledger above the chapters, from the story's own ledger: the ones still
// standing first and tinted, the rest after and muted. A chapter names only the setbacks that opened or ended in it, each
// line leading to its row, and repeats nothing still standing from an earlier chapter. The summary line reads the ledger.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, record } from "./plan-fixtures.mjs";

process.env.TZ = "UTC";

const NOW = "2026-11-20T21:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

/** A ledger row as the narrator writes it. */
function row(id, { subject, head, line, state = "closed", count = 1, first, last = first, since = null, endedAt = null, cost = null,
  waitingOn = null, opened, ended = null }) {
  return { id, subject, kind: "candidate.stopped", reason: null, threads: [], count, firstOpenedAt: first, lastOpenedAt: last, state,
    since, endedAt, endedBy: null, ending: state === "closed" ? "landing" : null, cost, waitingOn, chapters: { opened, ended },
    words: { head, line }, lastThread: null, lastOpenedIn: 0 };
}
/** A chapter as the narrator writes it, naming the ledger rows that opened or ended in it. */
function chapter(id, form, at, head, setbacks = [], standing = []) {
  return { id, form, subject: null, pieceTitle: null, at, entries: [], beats: [], setbacks, standing: [],
    notLanded: [], words: { head, line: "", standing } };
}
function storied(ledger, chapters, { decisions = 0 } = {}) {
  const document = record({ queue: [item("APP-3666", "merged"), item("APP-3667", "merged"), item("APP-3668", "queued")] });
  document.story = { schemaVersion: "story/1", state: "open", tones: [], beats: [], threads: [],
    digest: { landed: 2, pieces: 3, decisions, setbacks: { closed: 0, cost: 0, standing: [] },
      words: { head: "Two of three pieces landed so far.", line: "The narrator's own line." } },
    chapters, ...(ledger ? { ledger } : {}) };
  return document;
}
const open = (document, steps = []) => openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false, steps });
const ledgerRows = (app) => [...app.querySelectorAll("#happened .ledger-row")].map((li) => ({
  id: li.getAttribute("data-row"), cls: li.className, what: li.querySelector(".ledger-what").textContent,
  span: li.querySelector(".ledger-span").textContent, state: li.querySelector(".ledger-state").textContent }));
const lines = (app) => [...app.querySelectorAll("#happened .chapter-of")].map((li) => [li.getAttribute("data-chapter"),
  [...li.querySelectorAll(".chapter-setback a")].map((link) => [link.textContent, link.getAttribute("href")])]).filter(([, found]) => found.length);

// As this delivery stood: two stories stopped over and over while their merges failed, both since landed, and one stop
// that cost ten minutes before a fresh hand-off.
const THIS_DELIVERY = [
  row("s1", { subject: "APP-3668", head: "APP-3668 was stopped.", line: "It ended when lane 2 handed off APP-3668.", first: "2026-11-20T18:56:00Z",
    endedAt: "2026-11-20T19:06:00Z", cost: 600, opened: "c3", ended: "c3" }),
  row("s2", { subject: "APP-3667", head: "APP-3667 was stopped 6 times.", line: "It ended when APP-3667 landed.", count: 6,
    first: "2026-11-20T17:56:00Z", last: "2026-11-20T17:58:00Z", endedAt: "2026-11-20T18:00:00Z", cost: 0, opened: "c1", ended: "c2" }),
  row("s3", { subject: "APP-3666", head: "APP-3666 was stopped 6 times.", line: "It ended when APP-3666 landed.", count: 6,
    first: "2026-11-20T17:57:00Z", last: "2026-11-20T17:59:00Z", endedAt: "2026-11-20T18:00:00Z", cost: 0, opened: "c1", ended: "c2" }),
];
const THIS_CHAPTERS = [chapter("c1", "start", "2026-11-20T17:00:00Z", "Getting started", ["s2", "s3"]),
  chapter("c2", "landing", "2026-11-20T18:00:00Z", "APP-3667 and APP-3666 landed", ["s2", "s3"]),
  chapter("c3", "landing", "2026-11-20T19:20:00Z", "APP-3668 landed", ["s1"]), chapter("c4", "now", "2026-11-20T20:00:00Z", "Finishing")];

test("this delivery's ledger: every setback once, none standing, and the summary says nothing is stopped", async () => {
  const [{ app }] = await open(storied(THIS_DELIVERY, THIS_CHAPTERS, { decisions: 3 }));
  expect(app.querySelector("#happened .say").textContent).toBe("Nothing is stopped now. Three are over, costing 10m. The lead made three decisions.");
  expect(app.querySelector("#happened .ledger-title").textContent).toBe("Setbacks");
  expect(ledgerRows(app)).toEqual([
    { id: "s1", cls: "ledger-row is-closed", what: "APP-3668 was stopped.It ended when lane 2 handed off APP-3668.", span: "6:56 pm", state: "ended 7:06 pm · cost 10m" },
    { id: "s2", cls: "ledger-row is-closed", what: "APP-3667 was stopped 6 times.It ended when APP-3667 landed.", span: "5:56 pm–5:58 pm", state: "ended 6:00 pm" },
    { id: "s3", cls: "ledger-row is-closed", what: "APP-3666 was stopped 6 times.It ended when APP-3666 landed.", span: "5:57 pm–5:59 pm", state: "ended 6:00 pm" },
  ]);
  // Nothing in the stop colour: no row, no chapter line, and none of the old standing lines.
  expect(app.querySelectorAll("#happened .is-open, #happened .chapter-standing")).toHaveLength(0);
  // Each chapter names only what opened or ended in it, linked to its row; the finishing chapter repeats nothing.
  expect(lines(app)).toEqual([
    ["c3", [["APP-3668 was stopped · over", "#setback-s1"]]],
    ["c2", [["APP-3667 was stopped 6 times · ended here", "#setback-s2"], ["APP-3666 was stopped 6 times · ended here", "#setback-s3"]]],
    ["c1", [["APP-3667 was stopped 6 times · opened here, ended later", "#setback-s2"], ["APP-3666 was stopped 6 times · opened here, ended later", "#setback-s3"]]],
  ]);
  expect(app.querySelector('#happened [data-chapter="c4"] .chapter-setback')).toBeNull();
});

test("one standing setback comes first, tinted, with since when and who it waits on; the ended ones follow, muted", async () => {
  const ledger = [row("s1", { subject: "APP-3700", head: "APP-3700 was stopped.", line: "It still stands. It waits on the lead.", state: "open",
    first: "2026-11-20T20:10:00Z", since: "2026-11-20T20:10:00Z", waitingOn: "the lead", opened: "c4" }), ...THIS_DELIVERY.map((one, i) => ({ ...one, id: `s${i + 2}` }))];
  const chapters = THIS_CHAPTERS.map((one) => ({ ...one, setbacks: one.setbacks.map((name) => `s${Number(name.slice(1)) + 1}`) }));
  chapters[3].setbacks = ["s1"];
  const [{ app }] = await open(storied(ledger, chapters));
  const shown = ledgerRows(app);
  expect(shown.map(({ cls }) => cls)).toEqual(["ledger-row is-open", "ledger-row is-closed", "ledger-row is-closed", "ledger-row is-closed"]);
  expect(shown[0]).toEqual({ id: "s1", cls: "ledger-row is-open", what: "APP-3700 was stopped.It still stands. It waits on the lead.", span: "8:10 pm",
    state: "since 8:10 pm · waits on the lead" });
  expect(app.querySelector("#happened .say").textContent).toBe("One setback stands now. Three are over, costing 10m.");
  const line = app.querySelector('#happened [data-chapter="c4"] .chapter-setback');
  expect([line.className, line.textContent]).toEqual(["chapter-setback is-open", "APP-3700 was stopped · still stands"]);
});

test("the summary says what the ended setbacks cost together, not one figure per row", async () => {
  const ledger = THIS_DELIVERY.map((one, i) => ({ ...one, cost: [600, 240, 180][i] }));
  const [{ app }] = await open(storied(ledger, THIS_CHAPTERS));
  expect(app.querySelector("#happened .say").textContent).toBe("Nothing is stopped now. Three are over, costing 17m.");
});

test("a chapter's line and its ledger row are both reached from the keyboard", async () => {
  const [{ app }] = await open(storied(THIS_DELIVERY, THIS_CHAPTERS));
  // The line is an anchor with an address, so the tab order reaches it; each row is in the tab order too.
  const links = [...app.querySelectorAll("#happened .chapter-setback a")];
  expect(links.length).toBe(5);
  for (const link of links) expect(link.getAttribute("href")).toMatch(/^#setback-s\d$/);
  for (const li of app.querySelectorAll("#happened .ledger-row")) expect([li.id, li.getAttribute("tabindex")]).toEqual([li.id, "0"]);
  // Every link leads to a row that is on the page.
  for (const link of links) expect(app.querySelector(link.getAttribute("href"))).not.toBeNull();
});

test("following a chapter's line focuses its row in the ledger", async () => {
  let focused = null;
  await openPage(pageWithRuns(storied(THIS_DELIVERY, THIS_CHAPTERS), {}, { config: CONFIG }), { now: NOW, store: false,
    steps: [{ run: (window) => { window.document.querySelector('#happened [data-chapter="c3"] .chapter-setback a').click(); focused = window.document.activeElement.id; } }] });
  expect(focused).toBe("setback-s1");
});

test("the Setbacks filter shows the ledger and the chapters' setback lines; another filter shows neither", async () => {
  const [, problems, decisions, landings] = await open(storied(THIS_DELIVERY, THIS_CHAPTERS),
    [{ click: '#happened [data-tab="problems"]' }, { click: '#happened [data-tab="decisions"]' }, { click: '#happened [data-tab="landed"]' }]);
  expect(problems.app.querySelectorAll("#happened .ledger-row")).toHaveLength(3);
  expect(lines(problems.app).map(([name]) => name)).toEqual(["c3", "c2", "c1"]);
  // Only chapters with something to show under the filter: the finishing one has no setback line.
  expect([...problems.app.querySelectorAll("#happened .chapter-of")].map((li) => li.getAttribute("data-chapter"))).toEqual(["c3", "c2", "c1"]);
  expect(decisions.app.querySelector("#happened .setback-ledger")).toBeNull();
  expect(decisions.app.querySelectorAll("#happened .chapter-setback")).toHaveLength(0);
  // Landings lists the landing chapters, with neither the ledger nor their setback lines.
  expect([...landings.app.querySelectorAll("#happened .chapter-of")].map((li) => li.getAttribute("data-chapter"))).toEqual(["c3", "c2"]);
  expect(landings.app.querySelector("#happened .setback-ledger")).toBeNull();
  expect(landings.app.querySelectorAll("#happened .chapter-setback")).toHaveLength(0);
});

test("the summary agrees in number: several standing, one over, one decision", async () => {
  const ledger = [
    row("s1", { subject: "APP-1", head: "APP-1 was stopped.", line: "It still stands.", state: "open", first: "2026-11-20T20:10:00Z", since: "2026-11-20T20:10:00Z", opened: "c4" }),
    row("s2", { subject: "APP-2", head: "APP-2 was stopped.", line: "It still stands.", state: "open", first: "2026-11-20T20:05:00Z", since: "2026-11-20T20:05:00Z", opened: "c4" }),
    row("s3", { subject: "APP-3", head: "APP-3 was stopped.", line: "It ended.", first: "2026-11-20T19:00:00Z", endedAt: "2026-11-20T19:01:00Z", cost: 0, opened: "c3", ended: "c3" }),
  ];
  const [{ app }] = await open(storied(ledger, THIS_CHAPTERS, { decisions: 1 }));
  expect(app.querySelector("#happened .say").textContent).toBe("Two setbacks stand now. One is over. The lead made one decision.");
  // An open row that waits on no one says only since when; a repeated stop at one instant gives that instant once.
  const shown = ledgerRows(app);
  expect(shown[0].state).toBe("since 8:10 pm");
  const [{ app: same }] = await open(storied([row("s1", { subject: "APP-4", head: "APP-4 was stopped 2 times.", line: "It ended.", count: 2,
    first: "2026-11-20T19:00:00Z", last: "2026-11-20T19:00:00Z", endedAt: "2026-11-20T19:05:00Z", cost: 300, opened: "c3", ended: "c3" })], THIS_CHAPTERS));
  expect(ledgerRows(same)[0].span).toBe("7:00 pm");
  // One stop that ended has no span to give: its single time even when its last time is set apart.
  const [{ app: single }] = await open(storied([row("s1", { subject: "APP-5", head: "APP-5 was stopped.", line: "It ended.", count: 1,
    first: "2026-11-20T19:00:00Z", last: "2026-11-20T19:03:00Z", endedAt: "2026-11-20T19:05:00Z", cost: 300, opened: "c3", ended: "c3" })], THIS_CHAPTERS));
  expect(ledgerRows(single)[0].span).toBe("7:00 pm");
});

test("a chapter shown for its decision draws no setback line under the Decisions filter, and does under All", async () => {
  const chapters = structuredClone(THIS_CHAPTERS);
  chapters[2].beats = ["b1"];
  const document = storied(THIS_DELIVERY, chapters);
  document.story.beats = [{ id: "b1", form: "entry", tone: "decision", altitudes: ["headline", "story", "everything"], entries: [1],
    at: "2026-11-20T19:10:00Z", words: { head: "The lead chose to hand it off again.", line: "" } }];
  const [all, decisions] = await open(document, [{ click: '#happened [data-tab="decisions"]' }]);
  expect(all.app.querySelectorAll('#happened [data-chapter="c3"] .chapter-setback')).toHaveLength(1);
  const shown = decisions.app.querySelector('#happened [data-chapter="c3"]');
  expect(shown).not.toBeNull();
  expect(shown.querySelectorAll(".chapter-setback")).toHaveLength(0);
});

test("a delivery with no setbacks draws no ledger and says so; a story told before the ledger keeps its own lines", async () => {
  const [{ app: none }] = await open(storied([], [chapter("c1", "now", "2026-11-20T20:00:00Z", "Finishing")]));
  expect(none.querySelector("#happened .setback-ledger")).toBeNull();
  expect(none.querySelector("#happened .say").textContent).toBe("No setbacks.");
  const older = storied(null, [chapter("c1", "now", "2026-11-20T20:00:00Z", "Finishing", [], [{ thread: "t1", text: "A was stopped.", until: null }])]);
  const [{ app }] = await open(older);
  expect(app.querySelector("#happened .setback-ledger")).toBeNull();
  expect(app.querySelector("#happened .say").textContent).toBe("The narrator's own line.");
  expect(app.querySelector("#happened .chapter-standing").textContent).toBe("A was stopped.");
});
