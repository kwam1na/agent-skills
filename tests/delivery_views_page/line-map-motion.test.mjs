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

test("each stage has its icon, and only a piece being built shows the meshed gears", async () => {
  const document = record();
  const pieces = [...document.state.queue, ...document.state.planned].map((item) => [item.subject, item.stage]);
  expect(pieces.map(([, stage]) => stage).sort()).toEqual(["building", "checking", "merged", "planned", "queued", "review", "stopped", "syncing"]);
  const page = pageWithRuns(document, {}, { config: CONFIG });
  const drawn = {};
  for (const [subject, stage] of pieces) {
    const [, shown] = await openPage(page, { now: NOW, steps: [pick(subject)] });
    const icon = shown.app.querySelector("#detail .d-stage svg");
    expect(icon?.getAttribute("class")).toBe(`d-stage-icon is-${stage}`);
    expect(icon.getAttribute("aria-hidden")).toBe("true");
    const gears = [...icon.children].filter((child) => child.tagName.toLowerCase() === "g");
    if (stage === "building") {
      // Two gears, each its own group to turn about its centre: a dashed ring of teeth, a rim and an axle hole.
      expect(gears.map((gear) => gear.getAttribute("class"))).toEqual(["gear-big", "gear-small"]);
      for (const gear of gears) {
        const rings = [...gear.children];
        expect(rings.map((ring) => ring.tagName.toLowerCase())).toEqual(["circle", "circle", "circle"]);
        expect(rings.map((ring) => ring.hasAttribute("stroke-dasharray"))).toEqual([true, false, false]);
      }
      expect(icon.querySelectorAll("path")).toHaveLength(0);
    } else {
      expect(gears).toHaveLength(0);
      expect(icon.querySelectorAll("path").length > 0).toBe(true);
      drawn[stage] = JSON.stringify([...icon.querySelectorAll("path")].map((part) => part.getAttribute("d")));
    }
  }
  // Each stage draws its own icon; syncing and checking share the checking icon by design.
  expect(drawn.syncing).toBe(drawn.checking);
  const icons = Object.entries(drawn).filter(([stage]) => stage !== "syncing").map(([, paths]) => paths);
  expect(new Set(icons).size).toBe(icons.length);
  // A stage the page does not know gets no icon.
  const unknown = record();
  unknown.state.queue[2].stage = "mystery";
  const [, shown] = await openPage(pageWithRuns(unknown, {}, { config: CONFIG }), { now: NOW, steps: [pick(unknown.state.queue[2].subject)] });
  expect(shown.app.querySelector("#detail .d-stage svg")).toBeNull();
});

test("the gears turn only for a reader who has not asked for less motion", () => {
  const page = pageWithRuns(record(), {}, { config: CONFIG });
  const dom = new JSDOM(page);
  const turning = [];
  const walk = (rules, media) => {
    for (const rule of rules) {
      if (rule.cssRules && rule.media) { walk(rule.cssRules, rule.media.mediaText); continue; }
      if (rule.selectorText && /gear/.test(rule.selectorText) && (rule.style.getPropertyValue("animation") || rule.style.getPropertyValue("animation-name"))) {
        turning.push([rule.selectorText, rule.style.getPropertyValue("animation"), media]);
      }
    }
  };
  for (const sheet of dom.window.document.styleSheets) { walk(sheet.cssRules, null); }
  dom.window.close();
  // Meshed gears turn opposite ways, the small one faster, and only inside the no-preference query.
  expect(turning).toEqual([
    [".d-stage-icon .gear-big", "d-turn 6s linear infinite", "(prefers-reduced-motion: no-preference)"],
    [".d-stage-icon .gear-small", "d-turn 4s linear infinite reverse", "(prefers-reduced-motion: no-preference)"],
  ]);
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
  // The merge check's steps slide open too.
  const merged = await openPage(page, { now: NOW, motion: {}, steps: [pick("PAY-2358"), { click: "#detail details.mh summary" }] });
  expect(moved(merged.animations).some((animation) => animation.target === "mh")).toBe(true);
  // A reader who asked for less motion sees every change at once.
  const still = await openPage(page, { now: NOW, motion: { reduced: true }, steps: [pick("PAY-2363"), pick("PAY-2358"), { click: "#detail details.mh summary" }] });
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
