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
  expect(Object.keys(window.page).sort()).toEqual(["fold", "go", "here", "key", "motion", "open", "reasons", "redraw", "sideways", "start", "state"]);
  expect(window.page.key("stop", "PAY-2358")).toBe("stop:PAY-2358");
  expect(window.page.key("decision", 3)).toBe("decision:3");
  for (const [kind, id] of [["", "x"], [5, "x"], [{}, "x"], ["stop", ""], ["stop", undefined], ["stop", null]]) {
    expect((() => { try { window.page.key(kind, id); return null; } catch (error) { return error.name; } })()).toBe("TypeError");
  }
  dom.window.close();
});

/** page.js alone in a window whose frames are held until `frame()` runs them, and whose scroll the test moves. */
function controlled() {
  const frames = [];
  const view = { at: 0, height: 0, moves: [] };
  const dom = new JSDOM('<!doctype html><body><main id="app"></main></body>', { runScripts: "outside-only", beforeParse(window) {
    window.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
    Object.defineProperty(window, "scrollY", { configurable: true, get() { return view.at; } });
    Object.defineProperty(window, "innerHeight", { configurable: true, get() { return 300; } });
    Object.defineProperty(window.HTMLElement.prototype, "scrollHeight", { configurable: true, get() { return view.height; } });
    window.scrollTo = ({ top, behavior }) => { view.at = top; view.moves.push(behavior); };
    // Each element in the page's main is a 10-pixel row, in document order, holding the rows of what it holds.
    window.Element.prototype.getBoundingClientRect = function () {
      const rows = [...window.document.querySelectorAll("#app *")];
      const row = rows.indexOf(this);
      if (row < 0) return { top: 0, bottom: 0 };
      const held = this.querySelectorAll("*").length;
      return { top: row * 10 - view.at, bottom: (row + 1 + held) * 10 - view.at };
    };
  } });
  dom.window.eval(asset("page.js"));
  const frame = () => frames.splice(0).forEach((callback) => callback(0));
  return { window: dom.window, view, frame };
}

test("redraws of the record in use asked for within one frame merge into one at the next frame; a new record or status draws at once", () => {
  const { window, frame } = controlled();
  const drawn = [];
  window.page.redraw(() => drawn.push("tick"), "tick");
  window.page.redraw(() => drawn.push("runs"), "runs");
  window.page.redraw(() => drawn.push("width"), "width");
  expect(drawn).toEqual(["tick"]);
  frame();
  expect(drawn).toEqual(["tick", "width"]);
  // The frame after that is a new one: its first redraw draws at once.
  frame();
  window.page.redraw(() => drawn.push("later"), "tick");
  expect(drawn).toEqual(["tick", "width", "later"]);
  // A new record or status draws at once, in any frame, and the redraw waiting in that frame is no longer needed.
  window.page.redraw(() => drawn.push("plan"), "plan");
  window.page.redraw(() => drawn.push("status"), "status");
  window.page.redraw(() => drawn.push("pushed"), "push");
  window.page.redraw(() => drawn.push("pushed again"), "push");
  frame();
  expect(drawn).toEqual(["tick", "width", "later", "status", "pushed", "pushed again"]);
  for (const [draw, why] of [[null, "tick"], [() => {}, ""], [() => {}, undefined]]) {
    expect((() => { try { window.page.redraw(draw, why); return null; } catch (error) { return error.name; } })()).toBe("TypeError");
  }
  window.close();
});

test("with nothing keyed in view, a redraw puts the scroll back as far as the page now holds it", () => {
  for (const [height, kept] of [[2000, 800], [500, 200]]) {
    const { window, view } = controlled();
    view.at = 800;
    view.height = 2000;
    // The draw stands in for a browser that laid the page out short mid-draw, and the page's new height.
    window.page.redraw(() => { view.at = 0; view.height = height; }, "tick");
    expect(view.at).toBe(kept);
    // Put back at once: the page's stylesheet asks for smooth scrolling, which a kept place must not glide through.
    expect(view.moves).toEqual(["instant"]);
    window.close();
  }
  // The page's first draw opens its address, and keeps no place.
  const { window, view } = controlled();
  view.at = 800;
  view.height = 2000;
  window.page.redraw(() => { view.at = 0; }, "first");
  expect(view.at).toBe(0);
  window.close();
});

test("a mark the page carries twice never anchors: the reader keeps their place by the next element only one carries", () => {
  const { window, view } = controlled();
  const app = window.document.getElementById("app");
  const row = (attributes, words) => {
    const node = window.document.createElement("span");
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    node.textContent = words;
    return node;
  };
  // A header, a ticket named twice, and a stop below: the reader is 2px into the second mention.
  app.append(row({}, "header"), row({ "data-ticket": "PAY-1", class: "issue-link" }, "first"), row({}, "words"),
    row({ "data-ticket": "PAY-1", class: "issue-link", "data-read": "" }, "second"), row({ "data-subject": "PAY-9", class: "stop" }, "stop"));
  view.height = 1000;
  view.at = 32;
  const read = () => window.document.querySelector("[data-read]").getBoundingClientRect().top;
  expect(read()).toBe(-2);
  // The redraw names the ticket once more, above the reader, and grows the header: what they read stays where it was.
  window.page.redraw(() => { app.prepend(row({ "data-ticket": "PAY-1", class: "issue-link" }, "new"), row({}, "more header")); }, "tick");
  expect(read()).toBe(-2);
  expect(view.at).toBe(52);
  window.close();
});

test("each stable key anchors the reader: an id, a stop's subject, a ticket and a fold", () => {
  for (const key of ["id", "data-subject", "data-ticket", "data-fold"]) {
    const { window, view } = controlled();
    const app = window.document.getElementById("app");
    const row = (words) => { const node = window.document.createElement("p"); node.textContent = words; return node; };
    const read = window.document.createElement("p");
    read.setAttribute(key, "PAY-7");
    app.append(row("one"), row("two"), read, row("after"));
    view.height = 1000;
    view.at = 22;
    // Something grows above the element the reader is 2px into.
    window.page.redraw(() => { app.prepend(row("grew")); }, "tick");
    expect([key, read.getBoundingClientRect().top, view.at]).toEqual([key, -2, 32]);
    window.close();
  }
});

test("a mark carried twice before the redraw, or after it, is passed over for the next one only one element carries", () => {
  const setUp = (mentions) => {
    const { window, view } = controlled();
    const app = window.document.getElementById("app");
    const row = (attributes, words) => {
      const node = window.document.createElement("span");
      for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
      node.textContent = words;
      return node;
    };
    const mention = (extra = {}) => row({ "data-ticket": "PAY-2", class: "issue-link", ...extra }, "PAY-2");
    app.append(row({}, "header"), ...mentions(mention, row), row({ "data-subject": "PAY-9", class: "stop" }, "stop"));
    view.height = 1000;
    return { window, view, app, row, mention, stop: () => window.document.querySelector("[data-subject]").getBoundingClientRect().top };
  };
  // Twice before: the reader is on the second mention, which the redraw drops. The stop below keeps its place, where the
  // first mention, left alone, would have been put where the reader's stood.
  {
    const { window, view, stop } = setUp((mention, row) => [mention(), row({}, "words"), mention({ "data-read": "" })]);
    view.at = 31;
    const before = stop();
    window.page.redraw(() => { window.document.querySelector("[data-read]").remove(); }, "tick");
    expect(stop()).toBe(before);
    window.close();
  }
  // Once before, twice after: a new mention above is not the one the reader was on.
  {
    const { window, view, app, mention } = setUp((mention) => [mention({ "data-read": "" })]);
    view.at = 11;
    const read = () => window.document.querySelector("[data-read]").getBoundingClientRect().top;
    const before = read();
    window.page.redraw(() => { app.prepend(mention()); }, "tick");
    expect(read()).toBe(before);
    window.close();
  }
});

test("the anchor is the first marked element below the top, each part of a mark tells elements apart, and an ambiguous one gives way to what holds it", () => {
  const page = (build) => {
    const { window, view } = controlled();
    const app = window.document.getElementById("app");
    const make = (tag, attributes = {}, ...children) => {
      const node = window.document.createElement(tag);
      for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
      node.append(...children);
      return node;
    };
    view.height = 1000;
    build(app, make);
    const top = (selector) => window.document.querySelector(selector).getBoundingClientRect().top;
    return { window, view, app, make, top };
  };
  // Nothing marked across the top: the first marked element below anchors, not the last, though the redraw grows what
  // lies between them.
  {
    const { window, view, make, top } = page((app, make) => app.append(make("p"), make("p"), make("p", { "data-subject": "A" }),
      make("p", { "data-gap": "" }), make("p", { "data-subject": "B" })));
    view.at = 5;
    const before = top('[data-subject="A"]');
    window.page.redraw(() => { window.document.querySelector("[data-gap]").after(make("p"), make("p")); }, "tick");
    expect(top('[data-subject="A"]')).toBe(before);
    window.close();
  }
  // A ticket read in a span, and named elsewhere in a link (another tag) or a chip (another class): the span alone
  // carries its mark, so it anchors, though the redraw grows what lies above it inside its holder.
  for (const other of [["a", "issue-link"], ["span", "chip"]]) {
    const { window, view, make, top } = page((app, make) => app.append(
      make("section", { id: "holder" }, make("p", { "data-grow": "" }), make("span", { "data-ticket": "T", class: "issue-link", "data-read": "" })),
      make(other[0], { "data-ticket": "T", class: other[1] })));
    view.at = 22;
    const before = top("[data-read]");
    window.page.redraw(() => { window.document.querySelector("[data-grow]").after(make("p")); }, "tick");
    expect([other.join("."), top("[data-read]")]).toEqual([other.join("."), before]);
    window.close();
  }
  // A mention named twice across the top gives way to the marked element holding it, which keeps the reader's place
  // through what grows above the holder.
  {
    const { window, view, app, make, top } = page((app, make) => app.append(make("p"),
      make("div", { "data-fold": "F" }, make("p"), make("span", { "data-ticket": "T", class: "issue-link", "data-read": "" })),
      make("span", { "data-ticket": "T", class: "issue-link" })));
    view.at = 32;
    const before = top("[data-read]");
    window.page.redraw(() => { app.prepend(make("p")); }, "tick");
    expect(top("[data-read]")).toBe(before);
    window.close();
  }
  // A mark carried once before the redraw and twice after, the new one below the reader, does not anchor.
  {
    const { window, view, app, make, top } = page((app, make) => app.append(make("p"),
      make("span", { "data-ticket": "T", class: "issue-link", "data-read": "" }), make("p", { "data-subject": "S" })));
    view.at = 11;
    const before = top("[data-read]");
    window.page.redraw(() => { app.append(make("span", { "data-ticket": "T", class: "issue-link" })); }, "tick");
    expect(top("[data-read]")).toBe(before);
    window.close();
  }
});

test("the reader's own redraw draws at once and leaves a merged redraw still waiting for the next frame", () => {
  const { window, frame } = controlled();
  const drawn = [];
  window.page.redraw(() => drawn.push("tick"), "tick");
  window.page.redraw(() => drawn.push("runs"), "runs");
  window.page.redraw(() => drawn.push("depth"), "reader");
  expect(drawn).toEqual(["tick", "depth"]);
  frame();
  expect(drawn).toEqual(["tick", "depth", "runs"]);
  window.close();
});

test("two of the reader's own redraws in one frame both draw at once", () => {
  const { window } = controlled();
  const drawn = [];
  window.page.redraw(() => drawn.push("first"), "reader");
  window.page.redraw(() => drawn.push("second"), "reader");
  expect(drawn).toEqual(["first", "second"]);
  window.close();
});

/** Each view script that would end the inline script it is drawn into, as render.py inlines every one. */
const closesScript = (sources) => Object.entries(sources).filter(([, source]) => /<\/script/i.test(source)).map(([name]) => name);
test("no delivery-views script holds a literal </script>, which would end the script tag it is inlined into", () => {
  const assets = path.join(ROOT, "skills", "delivery-views", "assets");
  const sources = Object.fromEntries(readdirSync(assets).filter((name) => name.endsWith(".js")).map((name) => [name, readFileSync(path.join(assets, name), "utf8")]));
  const planted = closesScript({ ...sources, "runs.js": `${sources["runs.js"]}\nvar end = "</script>";` });
  expect([closesScript(sources), planted].map((names) => names.length ? `${names.join(", ")}: write <\\/script> in a string or pattern, as the HTML parser ends the script at </script>` : ""))
    .toEqual(["", "runs.js: write <\\/script> in a string or pattern, as the HTML parser ends the script at </script>"]);
});
