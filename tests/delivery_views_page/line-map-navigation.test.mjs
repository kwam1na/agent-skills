// Every navigation goes through the page controller's one location (page.go in page.js): in paged layout a page turn adds
// one history entry and nothing else does, Back and Forward step through the chapters each at its top, an outside address
// adds only the browser's own entry, and every navigation moves the page exactly once. Reasons are data, a feature adds
// its own from outside, and no asset outside page.js writes the scroll or the address.
process.env.TZ = "UTC";

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, ROOT, expect, openPage, pageWithRuns, readJson } from "./support.mjs";
import { item, previewDelivery, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-22T05:10:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
function corpus() {
  const source = path.join(CORPUS, "source");
  const journal = readFileSync(path.join(source, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(source, "state.json")), journal, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}
const MAIN = pageWithRuns(corpus(), {}, { config: CONFIG });
const ONE_PAGE = { "delivery-views:test:layout": "scroll" };
const open = (page, steps, scenario = {}) => openPage(page, { now: NOW, store: {}, layout: {}, rendered: page, ...scenario, steps });

/** A page whose plan has three headed decisions, the second naming the first, and two planned units. */
const DECIDED = (() => {
  const { document, plan } = previewDelivery({ rounds: 1, decisions: [
    { head: "An item no scope raised belongs to the queue holder.", body: ["Drafts to mark ready are the only such item."] },
    { head: "The supervisor words each item.", body: ["As decision 1 has it, it is said once."] },
    { head: "A third decision.", body: ["It has reasons too."] },
  ] });
  return pageWithRuns(document, {}, { config: CONFIG, plan });
})();
/** A page whose What happened tells one setback, linked from its chapter. */
const SETBACK = (() => {
  const document = record({ queue: [item("APP-1", "merged"), item("APP-2", "queued")] });
  const chapter = { id: "c1", form: "landing", subject: null, pieceTitle: null, at: "2026-10-22T04:00:00Z", entries: [], beats: [],
    setbacks: ["s1"], standing: [], notLanded: [], words: { head: "APP-1 landed", line: "", standing: [] } };
  const row = { id: "s1", subject: "APP-2", kind: "candidate.stopped", reason: null, threads: [], count: 1, firstOpenedAt: "2026-10-22T03:00:00Z",
    lastOpenedAt: "2026-10-22T03:00:00Z", state: "closed", since: null, endedAt: "2026-10-22T03:10:00Z", endedBy: null, ending: "landing",
    cost: 600, waitingOn: null, chapters: { opened: "c1", ended: "c1" }, words: { head: "APP-2 was stopped.", line: "It ended." },
    lastThread: null, lastOpenedIn: 0 };
  const landed = { id: "b0", form: "entry", tone: "landed", altitudes: ["headline", "story", "everything"], entries: [],
    at: "2026-10-22T04:00:00Z", words: { head: "APP-1 landed.", line: "" } };
  document.story = { schemaVersion: "story/1", state: "open", tones: [], beats: [landed], threads: [],
    digest: { landed: 1, pieces: 2, decisions: 0, setbacks: { closed: 1, cost: 600, standing: [] }, words: { head: "One of two.", line: "" } },
    chapters: [chapter], ledger: [row] };
  return pageWithRuns(document, {}, { config: CONFIG });
})();

/**
 * Counts what moves the page: every write of the window's or an element's scroll, and every focus that may scroll. The
 * count starts empty at each `mark()`.
 */
function recorder(window) {
  const moves = [];
  const wrap = (owner, name) => {
    const was = owner[name];
    owner[name] = function (...args) { moves.push(name); return typeof was === "function" ? was.apply(this, args) : undefined; };
  };
  for (const name of ["scrollTo", "scrollBy", "scroll"]) wrap(window, name);
  for (const name of ["scrollIntoView", "scrollTo", "scrollBy", "scroll"]) wrap(window.Element.prototype, name);
  for (const name of ["scrollTop", "scrollLeft"]) {
    const was = Object.getOwnPropertyDescriptor(window.Element.prototype, name);
    Object.defineProperty(window.Element.prototype, name, { configurable: true,
      get() { return was.get.call(this); }, set(value) { moves.push(name); was.set.call(this, value); } });
  }
  for (const owner of [window.HTMLElement.prototype, window.SVGElement.prototype]) {
    const focus = owner.focus;
    owner.focus = function (options) { if (!options || options.preventScroll !== true) moves.push("focus"); return focus.call(this, options); };
  }
  return moves;
}
/** Steps that run `act`, then record what it moved and where the page is, under `label`. */
function measured(seen, label, act, wait = 30) {
  let moves = null;
  return [
    { run(window) { moves = window.__moves ??= recorder(window); moves.length = 0; window.__length = window.history.length; act(window); }, wait },
    { run(window) {
      seen[label] = { moves: [...moves], grew: window.history.length - window.__length, hash: window.location.hash, y: window.scrollY,
        shown: window.document.querySelector(".chapter:not([hidden])")?.id, focused: window.document.activeElement };
    } },
  ];
}
const click = (selector) => (window) => window.document.querySelector(selector).click();
const press = (key) => (window) => window.document.body.dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
const outside = (hash) => (window) => { window.location.hash = hash; };
const touchBack = (window) => {
  const touch = (type, y) => { const event = new window.Event(type); Object.defineProperty(event, "touches", { value: y === null ? [] : [{ clientY: y }] }); window.dispatchEvent(event); };
  touch("touchstart", 100); touch("touchmove", 400); touch("touchend", null);
};
const launcherPage = (name) => (window) => {
  press("k")(window);
  window.document.body.dispatchEvent(new window.KeyboardEvent("keydown", { key: "k", metaKey: true, bubbles: true, cancelable: true }));
  const input = window.document.querySelector(".launcher-input");
  input.value = name;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
};

test("paged: each page turn adds one history entry; nothing else does", async () => {
  const seen = {};
  await open(MAIN, [
    ...measured(seen, "rail", click('.rail a[href="#line"]')),
    ...measured(seen, "tab", click('.chapter-tab-list a[href="#who"]')),
    ...measured(seen, "corner", click(".page-where")),
    ...measured(seen, "arrow", press("ArrowRight")),
    ...measured(seen, "scroll-past", touchBack),
    ...measured(seen, "launcher", launcherPage("What's left")),
    ...measured(seen, "layout", click(".chapter-tab-layout")),
  ]);
  for (const label of ["rail", "tab", "corner", "arrow", "scroll-past", "launcher"]) expect([label, seen[label].grew]).toEqual([label, 1]);
  expect([seen.rail.hash, seen.tab.hash, seen.corner.hash, seen.arrow.hash, seen["scroll-past"].hash, seen.launcher.hash])
    .toEqual(["#line", "#who", "#now", "#line", "#now", "#left"]);
  expect(seen.layout.grew).toBe(0);
  // Each reason, asked for directly, writes the address its row in page.reasons says: a push only for a turn, or a link
  // to another chapter.
  const grown = {};
  await open(MAIN, Object.entries({ turn: "line", "back-turn": "now", link: "who", address: "left", history: "happened", layout: "parked",
    first: "line", follow: "who" }).flatMap(([reason, chapter]) => measured(grown, reason, (window) => window.page.go({ chapter }, reason))));
  expect(Object.fromEntries(Object.entries(grown).map(([reason, entry]) => [reason, [entry.grew, entry.shown]]))).toEqual({
    turn: [1, "line"], "back-turn": [1, "now"], link: [1, "who"], address: [0, "left"], history: [0, "happened"], layout: [0, "parked"],
    first: [0, "line"], follow: [0, "who"] });
});

test("a tick, a push, an item link within the chapter shown and a link within reach add no entry; one into another chapter adds one, and Back returns", async () => {
  const seen = {};
  await open(MAIN, [
    ...measured(seen, "to left", click('.rail a[href="#left"]')),
    { tick: true }, { push: corpus() },
    ...measured(seen, "after tick and push", () => {}),
    ...measured(seen, "row", click("#left .left-title")),
    ...measured(seen, "back", (window) => window.history.back(), 60),
  ]);
  expect(seen["after tick and push"].grew).toBe(0);
  expect([seen.row.grew, seen.row.shown, seen.row.hash]).toEqual([1, "line", "#line"]);
  expect([seen.back.shown, seen.back.hash]).toEqual(["left", "#left"]);
  // Within the plan chapter, a decision reference to another decision adds none.
  const decided = {};
  await open(DECIDED, [
    { run(window) { window.document.querySelector('#plan .plan-fold[data-fold="decisions"]').open = true; } },
    ...measured(decided, "to plan", click('.rail a[href="#plan"]')),
    ...measured(decided, "reference", click("#plan .pd-ref")),
  ]);
  expect([decided["to plan"].grew, decided.reference.grew, decided.reference.hash]).toEqual([1, 0, "#plan-decision-1"]);
});

test("one-page: following the scroll, a chapter-tab link and a What's left row add no entry", async () => {
  const seen = {};
  await open(MAIN, [
    ...measured(seen, "tab", click('.chapter-tab-list a[href="#left"]')),
    ...measured(seen, "row", click("#left .left-title")),
  ], { stored: ONE_PAGE });
  expect([seen.tab.grew, seen.tab.hash, seen.row.grew, seen.row.hash]).toEqual([0, "#left", 0, "#line"]);
});

test("Back twice and Forward once step through the chapters in order, each at its top, with no entry added", async () => {
  const seen = {};
  await open(MAIN, [
    ...measured(seen, "line", click('.rail a[href="#line"]')),
    ...measured(seen, "left", click('.chapter-tab-list a[href="#left"]')),
    { run(window) { window.scrollTo({ top: 200, behavior: "instant" }); window.__length = window.history.length; } },
    ...measured(seen, "back", (window) => window.history.back(), 60),
    ...measured(seen, "back again", (window) => window.history.back(), 60),
    ...measured(seen, "forward", (window) => window.history.forward(), 60),
  ]);
  expect(["back", "back again", "forward"].map((label) => [seen[label].shown, seen[label].y, seen[label].grew]))
    .toEqual([["line", 0, 0], ["now", 0, 0], ["line", 0, 0]]);
});

test("an outside address change adds only the browser's entry and shows what it names, within the chapter shown too", async () => {
  const seen = {};
  await open(DECIDED, [
    ...measured(seen, "outside", outside("#left")),
    ...measured(seen, "to plan", click('.rail a[href="#plan"]')),
    ...measured(seen, "decision", outside("#plan-decision-3")),
  ]);
  expect([seen.outside.grew, seen.outside.shown]).toEqual([1, "left"]);
  expect([seen.decision.grew, seen.decision.shown, seen.decision.moves]).toEqual([1, "plan", ["scrollTo"]]);
});

test("a link to the page's own main, as the hosted skip link is, and a link to another document are left to the browser", async () => {
  const prevented = {};
  await open(MAIN, [{ run(window) {
    for (const [name, href] of [["skip", "#app"], ["away", "delivery-history.html"]]) {
      const link = window.document.createElement("a");
      link.href = href;
      link.textContent = name;
      window.document.body.prepend(link);
      // Read once the page's own handler on the document has had the click.
      window.addEventListener("click", (event) => { prevented[name] = event.defaultPrevented; event.preventDefault(); }, { once: true });
      link.click();
    }
  } }]);
  expect(prevented).toEqual({ skip: false, away: false });
});

test("every navigation makes exactly one move, and a link that takes focus takes it without scrolling", async () => {
  const seen = {};
  await open(MAIN, [
    ...measured(seen, "turn", click('.rail a[href="#line"]')),
    ...measured(seen, "row", (window) => { click('.rail a[href="#left"]')(window); window.__moves.length = 0; click("#left .left-title")(window); }),
    ...measured(seen, "back", (window) => window.history.back(), 60),
    ...measured(seen, "outside", outside("#who")),
  ]);
  for (const label of ["turn", "row", "back", "outside"]) expect([label, seen[label].moves]).toEqual([label, ["scrollTo"]]);
  const decided = {};
  await open(DECIDED, [
    { run(window) { window.document.querySelector('#plan .plan-fold[data-fold="decisions"]').open = true; } },
    ...measured(decided, "reference", click("#plan .pd-ref")),
    ...measured(decided, "unit", (window) => {
      window.document.querySelector('.stop-btn[data-subject="SHOP-92"]').click();
      window.__moves.length = 0;
      window.document.querySelector("#detail .d-plan").click();
    }),
  ]);
  expect([decided.reference.moves, decided.reference.focused.tagName, decided.reference.focused.closest("li").id])
    .toEqual([["scrollTo"], "SUMMARY", "plan-decision-1"]);
  expect([decided.unit.moves, decided.unit.shown]).toEqual([["scrollTo"], "plan"]);
  const setback = {};
  await open(SETBACK, [...measured(setback, "link", click("#happened .chapter-setback a"))]);
  expect([setback.link.moves, setback.link.focused.id]).toEqual([["scrollTo"], "setback-s1"]);
});

test("scrolling back up past a page's top lands at the previous page's end, instantly", async () => {
  const seen = {};
  const behaviours = [];
  await open(MAIN, [
    ...measured(seen, "line", click('.rail a[href="#line"]')),
    { run(window) { const was = window.scrollTo; window.scrollTo = (options) => { behaviours.push(options.behavior); was(options); }; } },
    ...measured(seen, "back up", touchBack),
    { run(window) { seen.most = window.document.documentElement.scrollHeight - window.innerHeight; } },
  ]);
  expect([seen["back up"].shown, seen["back up"].y]).toEqual(["now", seen.most]);
  expect(behaviours).toEqual(["instant"]);
});

test("an item link moves smoothly after a pointer press, and at once after a key press or under reduced motion", async () => {
  const behaviour = async (before, scenario = {}) => {
    const behaviours = [];
    await open(DECIDED, [
      { run(window) { window.document.querySelector('#plan .plan-fold[data-fold="decisions"]').open = true; click('.rail a[href="#plan"]')(window); } },
      { run(window) {
        before(window);
        const was = window.scrollTo;
        window.scrollTo = (options) => { behaviours.push(options.behavior); was(options); };
        click("#plan .pd-ref")(window);
      } },
    ], scenario);
    return behaviours;
  };
  const pointer = (window) => window.document.body.dispatchEvent(new window.Event("pointerdown", { bubbles: true }));
  expect(await behaviour(pointer)).toEqual(["smooth"]);
  expect(await behaviour(press("Tab"))).toEqual(["instant"]);
  expect(await behaviour(pointer, { motion: { reduced: true } })).toEqual(["instant"]);
});

test("the line keeps its distance from its right edge through a tick and a push that widens it", async () => {
  const at = [];
  const widen = (window) => {
    // The line is as wide as its stops, in a window 300 wide.
    Object.defineProperty(window.HTMLElement.prototype, "scrollWidth", { configurable: true,
      get() { return this.classList.contains("lines") ? 300 + 40 * this.querySelectorAll(".stop-btn, .mg-tick").length : 0; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { configurable: true,
      get() { return this.classList.contains("lines") ? 300 : 0; } });
  };
  const fromEnd = (window) => { const box = window.document.querySelector(".lines"); at.push(box.scrollWidth - box.clientWidth - box.scrollLeft); };
  const wider = corpus();
  wider.state.planned.push({ subject: "PAY-2990", title: "More", lane: "backend", stage: "planned" });
  await open(MAIN, [
    { run(window) { widen(window); } }, { tick: true },
    { run(window) { const box = window.document.querySelector(".lines"); box.scrollLeft = box.scrollWidth - box.clientWidth - 120; box.dispatchEvent(new window.Event("scroll")); fromEnd(window); } },
    { tick: true }, { run: fromEnd },
    { push: wider }, { run: fromEnd },
  ]);
  expect(at).toEqual([120, 120, 120]);
});

test("reasons are data: go names none of them, an unknown one throws, and one declared from outside behaves as a built-in", async () => {
  const source = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "page.js"), "utf8");
  const body = (text) => /\n  function go\(spot, reason\) \{\n([\s\S]*?)\n  \}\n/.exec(text)[1];
  const named = (text) => [...body(text).matchAll(/["']([a-z-]+)["']/g)].map((match) => match[1])
    .filter((name) => ["turn", "back-turn", "link", "address", "history", "layout", "first", "follow"].includes(name));
  expect(named(source)).toEqual([]);
  expect(named(source.replace("    var rule = Object", '    if (reason === "turn") { reason = "link"; }\n    var rule = Object'))).toEqual(["turn", "link"]);
  const seen = {};
  let thrown = null;
  const at = [];
  await open(MAIN, [
    { run(window) {
      try { window.page.go({ chapter: "line" }, "nope"); } catch (error) { thrown = error.name; }
      // A feature's own navigation and its own sideways box, through the page's public calls alone.
      window.page.reasons.peek = { address: "none", scroll: "item" };
      const box = window.document.createElement("div");
      Object.defineProperty(box, "scrollWidth", { get: () => 900 });
      Object.defineProperty(box, "clientWidth", { get: () => 300 });
      window.document.querySelector("#who .inner").append(box);
      window.page.sideways("peek-box", box);
      box.scrollLeft = 500;
      box.dispatchEvent(new window.Event("scroll"));
      window.__box = box;
    } },
    ...measured(seen, "peek", (window) => window.page.go({ chapter: "line", node: window.document.querySelector('#line .stop-btn[data-subject="PAY-2360"]') }, "peek")),
    { tick: true },
    { run(window) {
      const again = window.document.createElement("div");
      Object.defineProperty(again, "scrollWidth", { get: () => 900 });
      Object.defineProperty(again, "clientWidth", { get: () => 300 });
      window.page.sideways("peek-box", again);
      at.push(again.scrollLeft);
    } },
  ]);
  expect(thrown).toBe("TypeError");
  expect([seen.peek.moves, seen.peek.grew, seen.peek.shown]).toEqual([["scrollTo"], 0, "line"]);
  expect(at).toEqual([500]);
});

/** Every scroll or address write in the views' scripts outside page.js, as "file: write". */
const WRITES = [/scrollTo\b/, /scrollIntoView\b/, /scrollBy\(/, /\.scroll\(/, /scrollTop\s*=[^=]/, /scrollLeft\s*=[^=]/, /location\.hash\s*=[^=]/, /pushState\(/, /replaceState\(/];
function writes(sources) {
  const found = [];
  for (const [name, source] of Object.entries(sources)) {
    if (name === "page.js") continue;
    for (const line of source.split("\n")) {
      for (const pattern of WRITES) {
        if (!pattern.test(line)) continue;
        // The launcher's own list scrolls inside its fixed panel, not the page.
        if (name === "line-map.js" && /row\.scrollIntoView\(\{ block: "nearest" \}\)/.test(line)) continue;
        found.push(`${name}: ${pattern.source}`);
      }
    }
  }
  return found;
}
const smooth = (sheets) => Object.entries(sheets).filter(([, css]) => /scroll-behavior\s*:\s*smooth/.test(css)).map(([name]) => name);

test("no asset outside page.js writes the scroll or the address, and no stylesheet scrolls smoothly", () => {
  const assets = path.join(ROOT, "skills", "delivery-views", "assets");
  const read = (suffix) => Object.fromEntries(readdirSync(assets).filter((name) => name.endsWith(suffix)).map((name) => [name, readFileSync(path.join(assets, name), "utf8")]));
  const scripts = read(".js");
  const sheets = read(".css");
  const found = writes(scripts);
  expect(found.length === 0 ? "" : `${found.join(", ")}: move the page with page.go(location, reason), keep a sideways region with page.sideways(key, node)`).toBe("");
  expect(smooth(sheets).length === 0 ? "" : `${smooth(sheets).join(", ")} scrolls smoothly: page.go asks page.motion(true) for an item`).toBe("");
  // Each pin is shown red by a write planted in the line map.
  for (const planted of ["window.scrollTo(0, 0);", "node.scrollIntoView();", "box.scrollBy(1, 0);", "window.scroll(0, 0);", "box.scrollTop = 1;",
    "box.scrollLeft = 1;", "location.hash = id;", "history.pushState(null, \"\", \"#x\");", "history.replaceState(null, \"\", \"#x\");"]) {
    expect([planted, writes({ ...scripts, "line-map.js": `${scripts["line-map.js"]}\n${planted}` }).length]).toEqual([planted, 1]);
  }
  expect(smooth({ ...sheets, "line-map.css": `${sheets["line-map.css"]}\nhtml { scroll-behavior: smooth; }` })).toEqual(["line-map.css"]);
});
