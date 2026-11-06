// What the line map's design asks of its icons and its motion: each stage's icon, the gears that turn only for a reader
// who has not asked for less motion, panels that ease to their new height, and no native tooltips anywhere.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { JSDOM } from "jsdom";

import { CORPUS, REPLAYED_RUNS, expect, openPage, pageWithRuns, readJson, summarize } from "./support.mjs";
import { readings, reportView } from "./round-reports.mjs";

const SOURCE = path.join(CORPUS, "source");
const NOW = "2026-10-22T05:10:00Z";
const CONFIG = {
  spec: "delivery-views-config/1",
  tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^PAY-[0-9]+$" },
  forges: [{ name: "GitHub", repository: "acme/shop", pullUrl: "https://github.com/{repository}/pull/{number}", commitUrl: "https://github.com/{repository}/commit/{sha}" }],
  runs: REPLAYED_RUNS,
};
const SVG = "http://www.w3.org/2000/svg";

function record() {
  const journal = readFileSync(path.join(SOURCE, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return { state: readJson(path.join(SOURCE, "state.json")), journal, status: { source: "derived", text: "" }, focus: {}, stats: {} };
}

const pick = (subject) => ({ click: `[data-subject="${subject}"]` });

test("each stage has its mark, drawn in the state glyph grammar, and a landed piece the merge", async () => {
  const document = record();
  const pieces = [...document.state.queue, ...document.state.planned].map((item) => [item.subject, item.stage]);
  expect(pieces.map(([, stage]) => stage).sort()).toEqual(["building", "checking", "merged", "planned", "queued", "review", "stopped", "syncing"]);
  const page = pageWithRuns(document, {}, { config: CONFIG });
  for (const [subject, stage] of pieces) {
    const [, shown] = await openPage(page, { now: NOW, steps: [pick(subject)] });
    const icon = shown.app.querySelector("#detail .d-stage .d-stage-icon");
    expect(icon.getAttribute("aria-hidden")).toBe("true");
    if (stage === "merged") {
      // The merge is drawn on the persona grid, as the personas are.
      expect(icon.tagName.toLowerCase()).toBe("svg");
      expect(icon.getAttribute("class")).toBe("persona d-stage-icon is-merged");
    } else {
      // Every other stage is a mark the stylesheet draws from its class alone.
      expect(icon.tagName.toLowerCase()).toBe("span");
      expect(icon.getAttribute("class")).toBe(`d-stage-icon is-${stage}`);
      expect(icon.childNodes).toHaveLength(0);
    }
  }
  // A stage the page does not know gets no mark.
  const unknown = record();
  unknown.state.queue[2].stage = "mystery";
  const [, shown] = await openPage(pageWithRuns(unknown, {}, { config: CONFIG }), { now: NOW, steps: [pick(unknown.state.queue[2].subject)] });
  expect(shown.app.querySelector("#detail .d-stage .d-stage-icon")).toBeNull();
  // A stage the fixture does not hold still draws its own mark.
  const checked = record();
  checked.state.queue[2].stage = "checked";
  const [, done] = await openPage(pageWithRuns(checked, {}, { config: CONFIG }), { now: NOW, steps: [pick(checked.state.queue[2].subject)] });
  expect(done.app.querySelector("#detail .d-stage .d-stage-icon")?.getAttribute("class")).toBe("d-stage-icon is-checked");
});

test("the stage marks that turn do so only for a reader who has not asked for less motion", () => {
  const page = pageWithRuns(record(), {}, { config: CONFIG });
  const dom = new JSDOM(page);
  const turning = [];
  const walk = (rules, media) => {
    for (const rule of rules) {
      if (rule.cssRules && rule.media) { walk(rule.cssRules, rule.media.mediaText); continue; }
      if (rule.selectorText && /d-stage-icon/.test(rule.selectorText) && (rule.style.getPropertyValue("animation") || rule.style.getPropertyValue("animation-name"))) {
        turning.push([rule.selectorText, media]);
      }
    }
  };
  for (const sheet of dom.window.document.styleSheets) { walk(sheet.cssRules, null); }
  dom.window.close();
  // The stages where something is running turn, each by its own rule, and the ones at rest do not.
  for (const stage of ["syncing", "checking", "merging"]) {
    expect(turning.some(([selector]) => selector.includes(`.d-stage-icon.is-${stage}`))).toBe(true);
  }
  for (const stage of ["synced", "checked", "queued", "merged"]) {
    expect(turning.some(([selector]) => selector.includes(`.d-stage-icon.is-${stage}`))).toBe(false);
  }
  for (const [, media] of turning) { expect(media).toBe("(prefers-reduced-motion: no-preference)"); }
});

test("each stage's mark in the detail panel is drawn as the legend draws the stop it stands for", () => {
  const page = pageWithRuns(record(), {}, { config: CONFIG });
  const dom = new JSDOM(page);
  // Every declaration a selector receives from the stylesheet's top-level rules, the ones that draw the shape.
  const drawn = (selector) => {
    const found = {};
    for (const sheet of dom.window.document.styleSheets) {
      for (const rule of sheet.cssRules) {
        if (!rule.selectorText || !rule.selectorText.split(",").map((part) => part.trim()).includes(selector)) { continue; }
        for (let index = 0; index < rule.style.length; index += 1) {
          const property = rule.style[index];
          // Spacing places the mark beside its words, and the legend's own gap does that there.
          if (!/^margin/.test(property)) { found[property] = rule.style.getPropertyValue(property); }
        }
      }
    }
    return found;
  };
  const LEGEND = { planned: "d", building: "a", review: "a", pending: "p", queued: "r", syncing: "n", synced: "n",
    checking: "n", checked: "n", merging: "n", stopped: "h" };
  try {
    for (const [stage, entry] of Object.entries(LEGEND)) {
      for (const part of ["", "::after"]) {
        expect([stage + part, drawn(`.d-stage-icon.is-${stage}${part}`)]).toEqual([stage + part, drawn(`.line-legend i.${entry}${part}`)]);
      }
    }
    // In progress is the ring with its ripple partway out, never a ring around a centre dot.
    const ripple = drawn(".d-stage-icon.is-building::after");
    expect([ripple.inset, Number(ripple.opacity)]).toEqual(["-5px", 0.3]);
    expect(Object.keys(drawn(".d-stage-icon.is-building")).filter((property) => property.startsWith("background"))).toEqual([]);
  } finally { dom.window.close(); }
});

test("a newly picked piece's panel eases to its height, a live redraw of the same piece does not move, and nothing moves under reduced motion", async () => {
  const document = record();
  const page = pageWithRuns(document, {}, { config: CONFIG });
  const moved = (animations) => animations.filter((animation) => animation.options?.id === "run-height");
  // A live update that makes the same piece's panel taller: its ticket gains a summary.
  const redrawn = record();
  redrawn.state.tickets = { ...(redrawn.state.tickets || {}), "PAY-2363": { title: "Queued work", summary: "Now with a summary." } };
  const opened = await openPage(page, { now: NOW, motion: {}, steps: [pick("PAY-2358"), pick("PAY-2363"), { push: redrawn }] });
  const eased = moved(opened.animations).filter((animation) => animation.target === "detail");
  // The first view eases in its own pick, then each new pick eases once; the push of the same piece adds nothing.
  const afterPicks = eased.length;
  expect(afterPicks).toBe(3);
  const last = eased[eased.length - 1];
  const [from, to] = last.keyframes.map((frame) => frame.height);
  expect(from === to).toBe(false);
  expect(last.options.easing).toBe("cubic-bezier(.77, 0, .175, 1)");
  expect(last.options.duration >= 240 && last.options.duration <= 300).toBe(true);
  // The push redrew the same piece's panel, taller, and that did not ease.
  expect(opened[3].app.querySelectorAll("#detail .d2").length > opened[2].app.querySelectorAll("#detail .d2").length).toBe(true);
  const withoutPush = moved((await openPage(page, { now: NOW, motion: {}, steps: [pick("PAY-2358"), pick("PAY-2363")] })).animations)
    .filter((animation) => animation.target === "detail").length;
  expect(afterPicks).toBe(withoutPush);
  // The merge check's steps slide open and stay open once the slide ends; a second click slides them closed.
  const summary = { click: "#detail details.mh summary" };
  const isOpen = (snapshot) => snapshot.app.querySelector("#detail details.mh")?.hasAttribute("open");
  const merged = await openPage(page, { now: NOW, motion: {}, steps: [pick("PAY-2358"), summary, summary] });
  expect(merged.slice(1).map(isOpen)).toEqual([false, true, false]);
  const slides = moved(merged.animations).filter((animation) => animation.target === "mh");
  // Opening grows the box; closing starts where opening ended and shrinks it back to its summary.
  const [opening, closing] = slides.map((slide) => slide.keyframes.map((frame) => parseInt(frame.height, 10)));
  expect(slides).toHaveLength(2);
  expect(opening[1] > opening[0]).toBe(true);
  expect(closing[0]).toBe(opening[1]);
  expect(closing[1] < closing[0]).toBe(true);
  // A reader who asked for less motion sees every change at once, the merge check's opening and closing included.
  const still = await openPage(page, { now: NOW, motion: { reduced: true }, steps: [pick("PAY-2363"), pick("PAY-2358"), summary, summary] });
  expect(still.slice(2).map(isOpen)).toEqual([false, true, false]);
  expect(still.animations).toEqual([]);
});

test("nothing on the page carries a native tooltip, whichever part a reader opens", async () => {
  const summary = summarize(reportView(), { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 }, readings());
  const document = record();
  const withRun = document.state.queue.find((item) => item.runId === "run-7") ?? Object.assign(document.state.queue[2], { runId: "run-7" });
  const page = pageWithRuns(document, { "run-7": summary }, { config: CONFIG });
  expect(/\stitle=/.test(page)).toBe(false);
  const snapshots = await openPage(page, { now: NOW, steps: [
    pick(withRun.subject),
    { click: "#detail .run-pick" },
    pick("PAY-2358"),
    { click: "#detail details.mh summary" },
    { hover: `[data-subject="${withRun.subject}"]` },
    // The ticket card the page shows in place of a tooltip, and a filtered story.
    { hover: "#detail .issue-link" },
    { click: '[data-tab="problems"]' },
    { click: '[data-theme-pick="dark"]' },
  ] });
  expect(snapshots[1].app.querySelector("#detail .run-card")).not.toBeNull();
  expect(snapshots.map((snapshot) => snapshot.titled)).toEqual(snapshots.map(() => 0));
});
