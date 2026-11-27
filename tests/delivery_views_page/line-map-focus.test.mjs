// Focus by key: every control the line map draws carries a data-key, page.key(kind, id), named by what the record calls
// it, and the page controller's redraw puts focus back on the same key, in the same region, whatever was inserted above.
// Escape in the detail panel returns focus to the stop that opened it.
process.env.TZ = "UTC";

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, ROOT, expect, openPage, pageWithRuns, readJson, renderPage } from "./support.mjs";
import { item, planReviewDelivery, previewDelivery, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-22T05:10:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
function corpus() {
  const source = path.join(CORPUS, "source");
  const lines = readFileSync(path.join(source, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(source, "state.json")), journal: lines, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}
const MAIN = pageWithRuns(corpus(), {}, { config: CONFIG });
const PLANNED = (() => {
  const { document, plan } = previewDelivery({ rounds: 1, decisions: [
    { head: "An item no scope raised belongs to the queue holder.", body: ["Drafts to mark ready are the only such item."] },
    { head: "The supervisor words each item.", body: ["As decision 1 has it, it is said once."] },
  ] });
  return pageWithRuns(document, {}, { config: CONFIG, plan });
})();
const REVIEWED = (() => {
  const { document, plan } = planReviewDelivery();
  plan.approvals[0].text = "Product Owner signs off: the copy for every payment state is approved.";
  return pageWithRuns(document, {}, { config: CONFIG, plan });
})();
/** A story with a chapter, a landing and a setback in its ledger, so What happened draws its switches and its ledger. */
const STORIED = (() => {
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
/** Set-aside rows' words run past their two lines. */
const clamping = { run(window) {
  for (const [name, value] of [["scrollHeight", 60], ["clientHeight", 40]]) {
    Object.defineProperty(window.HTMLElement.prototype, name, { configurable: true,
      get() { return this.classList.contains("aside-head") ? value : 0; } });
  }
} };
const keyOf = (window) => window.document.activeElement?.getAttribute("data-key") ?? null;

/** The key focused on `selector`, and the key focused after a tick redrew the page. */
async function keeps(page, selector, { before = [], scenario = {} } = {}) {
  const seen = [];
  await openPage(page, { now: NOW, store: {}, ...scenario, steps: [
    ...before,
    { run(window) { window.document.querySelector(selector).focus(); seen.push(keyOf(window)); } },
    { tick: true }, ...(scenario.frames ? [{ frame: 16 }] : []),
    { run(window) { seen.push(keyOf(window), window.document.activeElement?.isConnected === true); } },
  ] });
  return seen;
}

test("every kind of control keeps focus through a redraw, on the same key", async () => {
  const kinds = [
    ["depth button", STORIED, '#happened .story-bar button[data-depth="everything"]'],
    ["tab", STORIED, '#happened .story-bar button[data-tab="landed"]'],
    ["ledger row", STORIED, "#happened .ledger-row"],
    ["plan row", REVIEWED, "#plan .plan-open li"],
    ["fold summary", REVIEWED, '#plan .plan-fold[data-fold="units"] > summary'],
    ["decision summary", PLANNED, "#plan-decision-2 summary", { before: [{ click: '#plan .plan-fold[data-fold="decisions"] > summary' }] }],
    ["set-aside row", REVIEWED, "#parked ol.aside > li", { before: [clamping, { frame: 16 }], scenario: { frames: true } }],
    ["stop", MAIN, '.stop-btn[data-subject="PAY-2360"]'],
    ["ticket", MAIN, "#line .stop-label .issue-link"],
  ];
  for (const [kind, page, selector, options] of kinds) {
    const [before, after, connected] = await keeps(page, selector, options);
    expect([kind, typeof before === "string" && before.length > 0, after, connected]).toEqual([kind, true, before, true]);
  }
});

test("focus follows the record, not the place: a row inserted above leaves it on the same story", async () => {
  const grown = corpus();
  // A story first in the queue: nearest to landing, it stands above the others in What's left and on its lane.
  grown.state.queue.unshift({ ...grown.state.queue.find((entry) => entry.stage === "queued"), order: 0, subject: "PAY-2900", title: "Earlier work",
    changeRequest: undefined });
  const said = (window) => [keyOf(window), window.document.activeElement?.textContent];
  const seen = [];
  for (const [selector, first] of [["#left .left-title", "#left .left-title"]]) {
    const at = {};
    await openPage(MAIN, { now: NOW, store: {}, steps: [
      { run(window) { window.document.querySelector(selector).focus(); at.before = said(window); at.firstBefore = window.document.querySelector(first).textContent; } },
      { push: grown },
      { run(window) { at.after = said(window); at.firstAfter = window.document.querySelector(first).textContent; } },
    ] });
    seen.push(at);
  }
  for (const at of seen) {
    // Something new stands above, and focus is still on the same story's control.
    expect([at.firstBefore !== at.firstAfter, at.after]).toEqual([true, at.before]);
  }
  expect(seen.map((at) => at.before[0].split(":")[0])).toEqual(["left"]);
});

test("one key in two places: focus comes back to the place it was in", async () => {
  const seen = {};
  await openPage(MAIN, { now: NOW, store: {}, steps: [
    { click: '.stop-btn[data-subject="PAY-2360"]' },
    { run(window) {
      const inDetail = window.document.querySelector('#detail .issue-link[data-ticket="PAY-2360"]');
      const atStop = [...window.document.querySelectorAll('#line .issue-link[data-ticket="PAY-2360"]')].find((node) => !node.closest("#detail"));
      seen.order = Boolean(atStop && inDetail && (atStop.compareDocumentPosition(inDetail) & 4));
      inDetail.focus();
    } },
    { tick: true },
    { run(window) { seen.after = [keyOf(window), Boolean(window.document.activeElement.closest("#detail"))]; } },
  ] });
  expect(seen).toEqual({ order: true, after: ["ticket:PAY-2360", true] });
});

test("Escape in the detail panel returns focus to the stop that opened it", async () => {
  const seen = {};
  await openPage(MAIN, { now: NOW, store: {}, steps: [
    { run(window) { window.document.querySelector('.stop-btn[data-subject="PAY-2360"]').focus(); } },
    { run(window) {
      window.document.querySelector("#detail .issue-link, #detail a, #detail button").focus();
      seen.inside = Boolean(window.document.activeElement.closest("#detail"));
      window.document.activeElement.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      seen.back = keyOf(window);
    } },
  ] });
  expect(seen).toEqual({ inside: true, back: "stop:PAY-2360" });
});

const FOCUSABLE = "#app :is(a[href], button, summary, input, select, [tabindex])";
test("every focusable control on every line-map fixture page carries a key", async () => {
  const cases = readdirSync(CORPUS).filter((name) => existsSync(path.join(CORPUS, name, "golden.json"))).sort();
  const config = readJson(path.join(CORPUS, "linked-config.json"));
  const missing = [];
  for (const name of cases) {
    const folder = path.join(CORPUS, name);
    const golden = readJson(path.join(folder, "golden.json"));
    const planFile = path.join(folder, "plan.json");
    const page = renderPage(readJson(path.join(folder, "document.json")), { config, runsCommands: readJson(path.join(folder, "runs-commands.json")),
      plan: existsSync(planFile) ? readJson(planFile) : null });
    await openPage(page, { now: golden.now, steps: [{ run(window) {
      for (const node of window.document.querySelectorAll(FOCUSABLE)) {
        if (!node.getAttribute("data-key")) missing.push(`${name}: ${node.tagName.toLowerCase()}.${(node.getAttribute("class") || "").split(" ")[0]}`);
      }
    } }] });
  }
  expect(missing.slice(0, 10)).toEqual([]);
});

/** Each place outside page.js that writes a data-key other than through page.key, as "file: line". */
function unkeyed(sources) {
  const found = [];
  for (const [name, source] of Object.entries(sources)) {
    if (name === "page.js") continue;
    source.split("\n").forEach((line, index) => {
      if (!/data-key/.test(line)) return;
      if (/(setAttribute\(\s*"data-key",\s*page\.key\(|"data-key":\s*page\.key\()/.test(line)) return;
      // Reads are not writes.
      if (/(getAttribute|hasAttribute|querySelector(All)?|closest)\(\s*["'][^"']*data-key/.test(line)) return;
      if (/^\s*\/\//.test(line)) return;
      found.push(`${name}:${index + 1}`);
    });
  }
  return found;
}

test("keys are set only through page.key", () => {
  const assets = path.join(ROOT, "skills", "delivery-views", "assets");
  const sources = Object.fromEntries(readdirSync(assets).filter((name) => name.endsWith(".js")).map((name) => [name, readFileSync(path.join(assets, name), "utf8")]));
  const found = unkeyed(sources);
  expect(found.length === 0 ? "" : `${found.join(", ")}: key a control with page.key(kind, id), the record's own id for it`).toBe("");
  for (const planted of ['node.setAttribute("data-key", "stop:" + key);', 'node.dataset.key = key;', 'shape(svg, "g", { "data-key": "tick:" + key });']) {
    expect([planted, unkeyed({ ...sources, "line-map.js": `${sources["line-map.js"]}\n${planted}` }).length > 0 || /dataset\.key/.test(planted)]).toEqual([planted, true]);
  }
});

test("a control a feature keys through page.key keeps focus through the feature's own redraw", async () => {
  const seen = {};
  await openPage(MAIN, { now: NOW, store: {}, steps: [{ run(window) {
    const host = window.document.getElementById("who");
    const make = () => {
      const button = window.document.createElement("button");
      button.textContent = "Probe";
      button.setAttribute("data-key", window.page.key("probe", "x"));
      return button;
    };
    let shown = make();
    host.append(shown);
    shown.focus();
    // The feature draws its control again, as a redraw does, through the page's one redraw transaction.
    window.page.redraw(() => { const next = make(); shown.replaceWith(next); shown = next; }, "reader");
    seen.after = [keyOf(window), window.document.activeElement === shown];
  } }] });
  expect(seen).toEqual({ after: ["probe:x", true] });
});
