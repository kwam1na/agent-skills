// What is open, closed or picked lives in the page controller's one store (page.state in page.js), and every fold and
// disclosure is page.fold: each survives a redraw, saved state keeps the keys and formats the page has always written,
// a link to anything inside a closed fold opens it, and pointing names and lights but never picks.
process.env.TZ = "UTC";

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, ROOT, expect, openPage, pageWithRuns, readJson } from "./support.mjs";
import { item, planReviewDelivery, previewDelivery, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-22T05:10:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
function corpus() {
  const source = path.join(CORPUS, "source");
  const lines = readFileSync(path.join(source, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(source, "state.json")), journal: lines, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}
const MAIN = pageWithRuns(corpus(), {}, { config: CONFIG });
/** A plan with three headed decisions, two units, a blocking approval that says more than its title, and set-aside rows. */
const PLANNED = (() => {
  const { document, plan } = previewDelivery({ rounds: 1, decisions: [
    { head: "An item no scope raised belongs to the queue holder.", body: ["Drafts to mark ready are the only such item."] },
    { head: "The supervisor words each item.", body: ["As decision 1 has it, it is said once."] },
    { head: "A third decision.", body: ["It has reasons too."] },
  ] });
  plan.approvals = [{ id: "O25", text: "O25 Owner signs off: the copy for every payment state is approved.", state: "waiting", blocking: true }];
  plan.outOfScope = ["A per-delivery base branch that runs on past two lines when the page is narrow enough to clamp it."];
  return pageWithRuns(document, {}, { config: CONFIG, plan });
})();
/** A plan under review: a blocking approval that says more than its title, units, and two rows the plan set aside. */
const REVIEWED = (() => {
  const { document, plan } = planReviewDelivery();
  plan.approvals[0].text = "Product Owner signs off: the copy for every payment state is approved.";
  return pageWithRuns(document, {}, { config: CONFIG, plan });
})();
/** A record whose story has a chapter and a landing, so What happened draws its depth and filter switches, with a plan. */
const STORIED = (() => {
  const document = record({ queue: [item("APP-1", "merged"), item("APP-2", "queued")] });
  const chapter = { id: "c1", form: "landing", subject: null, pieceTitle: null, at: "2026-10-22T04:00:00Z", entries: [], beats: [],
    setbacks: [], standing: [], notLanded: [], words: { head: "APP-1 landed", line: "", standing: [] } };
  const landed = { id: "b0", form: "entry", tone: "landed", altitudes: ["headline", "story", "everything"], entries: [],
    at: "2026-10-22T04:00:00Z", words: { head: "APP-1 landed.", line: "" } };
  document.story = { schemaVersion: "story/1", state: "open", tones: [], beats: [landed], threads: [],
    digest: { landed: 1, pieces: 2, decisions: 0, setbacks: { closed: 0, cost: 0, standing: [] }, words: { head: "One of two.", line: "" } },
    chapters: [chapter] };
  return pageWithRuns(document, {}, { config: CONFIG, plan: planReviewDelivery().plan });
})();
/** Set-aside rows' words run past their two lines: each row's head holds more than it shows. */
const clamping = (window) => {
  for (const [name, value] of [["scrollHeight", 60], ["clientHeight", 40]]) {
    Object.defineProperty(window.HTMLElement.prototype, name, { configurable: true,
      get() { return this.classList.contains("aside-head") ? value : 0; } });
  }
};
const open = (page, steps, scenario = {}) => openPage(page, { now: NOW, store: {}, ...scenario, steps });
const shot = (snaps) => snaps[snaps.length - 1].app;
const isOpen = (app, selector) => app.querySelector(selector)?.hasAttribute("open");

test("every fold, row and pick a reader leaves is as they left it after a redraw", async () => {
  const decided = await open(PLANNED, [
    { click: '#plan .plan-fold[data-fold="decisions"] > summary' },
    { click: "#plan-decision-2 summary" },
    { tick: true },
  ]);
  expect([isOpen(shot(decided), '#plan .plan-fold[data-fold="decisions"]'), isOpen(shot(decided), "#plan-decision-2 details")]).toEqual([true, true]);
  // An approval's row, the units fold and a set-aside row whose words run past two lines; the page's frames are held, so
  // a frame measures the rows and lets the tick draw, and another measures the redrawn rows.
  const reviewed = await open(REVIEWED, [
    { run: clamping }, { frame: 16 },
    { click: "#plan .ap-row:not(.is-whole) > summary" },
    { click: '#plan .plan-fold[data-fold="units"] > summary' },
    { click: "#parked ol.aside > li" },
    { tick: true }, { frame: 16 }, { frame: 16 },
  ], { frames: true });
  const after = shot(reviewed);
  const aside = after.querySelector("#parked ol.aside > li");
  expect([isOpen(after, "#plan .ap-row:not(.is-whole)"), isOpen(after, '#plan .plan-fold[data-fold="units"]'),
    aside.classList.contains("clamps"), aside.getAttribute("aria-expanded")]).toEqual([true, true, true, "true"]);
  // The story's filter, through a tick.
  const filtered = await open(STORIED, [{ click: '#happened .story-bar button[data-tab="landed"]' }, { tick: true }]);
  expect(shot(filtered).querySelector('#happened .story-bar button[data-tab="landed"]').getAttribute("aria-pressed")).toBe("true");
  let lit = null;
  // A stop picked by a click, and the lane the pointer lights, through a tick and a push. Focus leaves the stop first, so
  // only the store, not focus coming back to it, can keep the pick; the lit lane is another lane's.
  const line = await open(MAIN, [
    { click: '.stop-btn[data-subject="PAY-2360"]' },
    { run(window) {
      window.document.activeElement.blur();
      const lane = window.document.querySelector('.stop-btn[data-subject="PAY-2360"]').closest(".line").getAttribute("data-lane");
      window.__lit = [...window.document.querySelectorAll("#line .line[data-lane]")].map((row) => row.getAttribute("data-lane")).find((other) => other !== lane);
      window.document.querySelector(`#line .line[data-lane="${window.__lit}"]`).dispatchEvent(new window.Event("mouseenter"));
    } },
    { tick: true }, { push: corpus() },
    { run(window) { lit = [window.document.querySelector(".lines-track").classList.contains("lit"),
      window.document.querySelector(`.lines-track [data-lane="${window.__lit}"]`).classList.contains("on"), window.document.activeElement === window.document.body]; } },
  ]);
  const drawn = line[line.length - 2].app;
  expect([drawn.querySelector('.stop-btn[data-subject="PAY-2360"]').getAttribute("aria-pressed"),
    drawn.querySelector("#detail .d1")?.textContent === line[1].app.querySelector("#detail .d1")?.textContent]).toEqual(["true", true]);
  expect(lit).toEqual([true, true, true]);
});

test("saved state the page has always written is read, and written back in the same keys and formats", async () => {
  // Literal keys and values, as today's page writes them: nothing here is read from the asset.
  const stored = { "delivery-views:test:layout": "scroll", "delivery-views:test:story-depth": "everything",
    "delivery-views:test:plan-folds": JSON.stringify(["units"]) };
  const seen = {};
  await open(STORIED, [
    { run(window) {
      seen.read = [window.document.body.classList.contains("paged"),
        window.document.querySelector('#happened .story-bar button[data-depth="everything"]').getAttribute("aria-pressed"),
        window.document.querySelector('#plan .plan-fold[data-fold="units"]').open];
    } },
    { click: ".chapter-tab-layout" },
    { click: '#happened .story-bar button[data-depth="headline"]' },
    { click: '#plan .plan-fold[data-fold="units"] > summary' },
    { click: '#plan .plan-fold[data-fold="out"] > summary' },
    { run(window) { seen.written = { ...window.localStorage }; } },
  ], { rendered: STORIED, stored });
  expect(seen.read).toEqual([false, "true", true]);
  expect(seen.written).toEqual({ "delivery-views:test:layout": "paged", "delivery-views:test:story-depth": "headline",
    "delivery-views:test:plan-folds": JSON.stringify(["out"]) });
});

test("a link into a closed fold opens every fold holding it, scrolls once, and the folds stay open through a tick", async () => {
  const moves = [];
  const record = (window) => {
    const was = window.scrollTo;
    window.scrollTo = (options) => { moves.push(options.behavior); was(options); };
  };
  let saved = null;
  const fresh = await open(PLANNED, [{ run: record }, { run(window) { saved = window.localStorage.getItem("delivery-views:test:plan-folds"); } }, { tick: true }],
    { layout: {}, rendered: PLANNED, address: "http://localhost/view/line-map.html#plan-decision-3" });
  // The plan fold the link opened is saved at once, before any redraw, as the reader will find it on their next visit.
  expect(JSON.parse(saved)).toContain("decisions");
  for (const app of [fresh[0].app, shot(fresh)]) {
    expect([isOpen(app, '#plan .plan-fold[data-fold="decisions"]'), isOpen(app, "#plan-decision-3 details")]).toEqual([true, true]);
  }
  // The same through a link followed in the page.
  const followed = await open(PLANNED, [
    { run(window) { window.document.querySelector('.rail a[href="#plan"]').click(); record(window); } },
    { run(window) { window.page.go({ item: "plan-decision-3" }, "link"); } },
    { tick: true },
  ], { layout: {}, rendered: PLANNED });
  expect(moves).toEqual(["smooth"]);
  const last = shot(followed);
  expect([isOpen(last, '#plan .plan-fold[data-fold="decisions"]'), isOpen(last, "#plan-decision-3 details")]).toEqual([true, true]);
});

test("pointing at a stop or a tick names and lights it but never picks it, before or after a push; a click and focus pick", async () => {
  const seen = [];
  const read = (label) => ({ run(window) {
    seen.push([label, window.document.querySelector("#detail .d1")?.textContent,
      [...window.document.querySelectorAll("#line [aria-pressed]")].map((node) => node.getAttribute("aria-pressed")).join("")]);
  } });
  const hover = (selector) => ({ run(window) { window.document.querySelector(selector).dispatchEvent(new window.Event("mouseenter")); } });
  await open(MAIN, [
    { click: '.stop-btn[data-subject="PAY-2360"]' }, read("picked"),
    hover('.stop-btn[data-subject="PAY-2362"]'), read("stop pointed"),
    hover('.mg-tick[data-subject="PAY-2358"]'), read("tick pointed"),
    { push: corpus() }, read("pushed"),
    hover('.stop-btn[data-subject="PAY-2362"]'), hover('.mg-tick[data-subject="PAY-2358"]'), read("pointed after"),
  ]);
  const [picked, ...rest] = seen;
  for (const entry of rest) expect([entry[0], entry[1], entry[2]]).toEqual([entry[0], picked[1], picked[2]]);
  // Pointing at a stop lights its lane, and leaving it lets the lane go.
  const lit = [];
  await open(MAIN, [{ run(window) {
    const stop = window.document.querySelector('.stop-btn[data-subject="PAY-2362"]');
    const lane = stop.closest(".line").getAttribute("data-lane");
    const track = window.document.querySelector(".lines-track");
    stop.dispatchEvent(new window.Event("mouseenter"));
    lit.push([track.classList.contains("lit"), track.querySelector(`[data-lane="${lane}"]`).classList.contains("on")]);
    stop.dispatchEvent(new window.Event("mouseleave"));
    lit.push([track.classList.contains("lit"), track.querySelector(`[data-lane="${lane}"]`).classList.contains("on")]);
  } }]);
  expect(lit).toEqual([[true, true], [false, false]]);
  // A click on a tick, and keyboard focus on a stop, each pick.
  const picks = [];
  await open(MAIN, [
    { click: '.mg-tick[data-subject="PAY-2358"]' }, { run(window) { picks.push(window.document.querySelector('.mg-tick[data-subject="PAY-2358"]').getAttribute("aria-pressed")); } },
    { run(window) { window.document.querySelector('.stop-btn[data-subject="PAY-2362"]').focus(); picks.push(window.document.querySelector('.stop-btn[data-subject="PAY-2362"]').getAttribute("aria-pressed")); } },
  ]);
  expect(picks).toEqual(["true", "true"]);
});

/** Each fold outside page.js that opens or shuts itself, as "file: what". */
const SELF_TOGGLES = [/addEventListener\(\s*["']toggle["']/, /\bontoggle\b/, /\.open\s*=[^=]/, /classList\.(add|remove|toggle)\(\s*["']open["']/,
  /(set|remove|toggle)Attribute\(\s*["']open["']/];
function selfToggles(sources) {
  const found = [];
  for (const [name, source] of Object.entries(sources)) {
    if (name === "page.js") continue;
    for (const line of source.split("\n")) {
      // The theme switch's holder is a menu, not a fold.
      if (name === "line-map.js" && /holder\.classList\.toggle\("open", open\);/.test(line)) continue;
      for (const pattern of SELF_TOGGLES) if (pattern.test(line)) found.push(`${name}: ${pattern.source}`);
    }
  }
  return found;
}

test("no fold outside page.js opens or shuts itself", () => {
  const assets = path.join(ROOT, "skills", "delivery-views", "assets");
  const sources = Object.fromEntries(readdirSync(assets).filter((name) => name.endsWith(".js")).map((name) => [name, readFileSync(path.join(assets, name), "utf8")]));
  const found = selfToggles(sources);
  expect(found.length === 0 ? "" : `${found.join(", ")}: make it a fold with page.fold(node, {key}), and open it with page.open(node, open)`).toBe("");
  for (const planted of ['fold.addEventListener("toggle", remember);', "fold.ontoggle = remember;", "fold.open = true;", 'row.classList.add("open");',
    'fold.setAttribute("open", "");', 'fold.removeAttribute("open");', 'fold.toggleAttribute("open", true);']) {
    expect([planted, selfToggles({ ...sources, "line-map.js": `${sources["line-map.js"]}\n${planted}` }).length]).toEqual([planted, 1]);
  }
});

test("a fold and a piece of state added from outside, through page alone, are opened by a link and kept through a tick", async () => {
  const seen = {};
  await open(MAIN, [
    { run(window) {
      const doc = window.document;
      // Beside the chapter's redrawn part, so the page's own draw leaves it be, as a feature's own part would sit.
      const box = doc.createElement("details");
      const summary = doc.createElement("summary");
      summary.textContent = "A feature's fold";
      const inside = doc.createElement("p");
      inside.id = "feature-inside";
      inside.textContent = "Inside it";
      box.append(summary, inside);
      doc.getElementById("who").append(box);
      window.page.fold(box, { key: "feature:box" });
      window.page.state("feature:picked").set("x");
      window.__box = box;
      // And a details element the page never made a fold: a link into it still opens it.
      const plain = doc.createElement("details");
      const said = doc.createElement("p");
      said.id = "plain-inside";
      plain.append(doc.createElement("summary"), said);
      doc.getElementById("who").append(plain);
      window.__plain = plain;
    } },
    { run(window) { window.page.go({ item: "feature-inside" }, "link"); seen.opened = window.__box.open; } },
    { run(window) { window.page.go({ item: "plain-inside" }, "link"); seen.plain = window.__plain.open; } },
    { tick: true },
    { run(window) { seen.kept = [window.__box.open, window.page.state("feature:box").get(), window.page.state("fold:feature:box").get(), window.page.state("feature:picked").get()]; } },
  ]);
  expect(seen).toEqual({ opened: true, plain: true, kept: [true, undefined, true, "x"] });
});

test("a set-aside row's click still reaches the page, so it shuts an open menu; a decision the browser opens is counted", async () => {
  const seen = {};
  await open(REVIEWED, [
    { click: ".chapter-tab-button" },
    { run(window) {
      seen.menu = window.document.querySelector(".chapter-tab-list").hidden;
      const row = window.document.querySelector("#parked ol.aside > li");
      row.classList.add("clamps");
      row.click();
      seen.after = [window.document.querySelector(".chapter-tab-list").hidden, row.getAttribute("aria-expanded")];
    } },
  ], { frames: true });
  expect(seen).toEqual({ menu: false, after: [true, "true"] });
  // Every decision opened by the browser itself, as find in page opens one: the control then offers to close them all.
  const label = await open(PLANNED, [
    { click: '#plan .plan-fold[data-fold="decisions"] > summary' },
    { toggle: "#plan-decision-1 details" }, { toggle: "#plan-decision-2 details" }, { toggle: "#plan-decision-3 details" },
  ]);
  expect(shot(label).querySelector("#plan .pd-all").textContent).toBe("Close all");
});

test("a set-aside row opens by Enter or Space when its words run past two lines, and not at all when they don't; a whole approval stays shut", async () => {
  const seen = {};
  await open(REVIEWED, [
    { run: clamping }, { frame: 16 },
    { run(window) {
      const [long, short] = window.document.querySelectorAll("#parked ol.aside > li");
      long.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
      seen.enter = long.getAttribute("aria-expanded");
      long.dispatchEvent(new window.KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
      seen.space = long.getAttribute("aria-expanded");
      short.classList.remove("clamps");
      short.click();
      seen.short = short.getAttribute("aria-expanded");
      const whole = window.document.querySelector("#plan .ap-row.is-whole");
      whole.querySelector("summary").click();
      seen.whole = whole.open;
    } },
  ], { frames: true });
  expect(seen).toEqual({ enter: "true", space: "false", short: "false", whole: false });
});

test("a decision the browser shuts after all were open turns the control back to Open all", async () => {
  const label = await open(PLANNED, [
    { click: '#plan .plan-fold[data-fold="decisions"] > summary' },
    { toggle: "#plan-decision-1 details" }, { toggle: "#plan-decision-2 details" }, { toggle: "#plan-decision-3 details" },
    { toggle: "#plan-decision-2 details" },
  ]);
  expect([label[label.length - 2].app.querySelector("#plan .pd-all").textContent, shot(label).querySelector("#plan .pd-all").textContent])
    .toEqual(["Close all", "Open all"]);
});

test("a deferral row's click stays inside its card", async () => {
  const head = "b".repeat(40);
  const lens = "lens.adversarial-testing";
  const outcome = { round: 1, outcome: "aligned", entries: [{ lens, state: "obtained", binding: { subjectRef: "WORK-1", candidateRef: head, round: 1 },
    result: { schemaVersion: "review-lens-result/1", lensId: lens, subjectRef: "WORK-1", candidateRef: head, round: 1, evidence: { observed: true },
      outcome: "aligned", findings: [{ id: "at-1", severity: "P3", summary: "A note for later.", state: "deferred", deferredIssue: "Follow up on at-1." }],
      resolvedFindings: [] } }] };
  const document = { state: { lanes: [], queue: [{ subject: "WORK-1", title: "Work", stage: "merged", head, runId: null }], planned: [] },
    journal: [], reviewEvidence: { "WORK-1": { candidateRef: head, digest: "d".repeat(64), outcomes: [outcome] } } };
  const seen = {};
  await open(pageWithRuns(document, {}, { config: CONFIG }), [
    { click: '[data-subject="WORK-1"]' },
    { run(window) {
      let reached = 0;
      window.document.addEventListener("click", () => { reached += 1; });
      const toggle = window.document.querySelector("#detail li.is-deferral .run-deferral");
      toggle.click();
      seen.deferral = [reached, toggle.getAttribute("aria-expanded")];
    } },
  ], { store: false });
  expect(seen.deferral).toEqual([0, "true"]);
});
