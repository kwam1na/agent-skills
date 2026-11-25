// The line keeps its words and marks where they belong: a stop ready to be checked sits on its line like any other, the
// finish post's label is drawn whole inside the merge drawing, and it never sits on the tally of what has landed.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, waited } from "./support.mjs";

const NOW = "2026-11-17T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
// "Finish line" at the label's 13px is about 63px wide, so it reaches this far past the post it is centred on.
const LABEL_HALF = 36;
// The tally is 11px Geist Mono: 0.6em a character.
const TALLY_CHAR = 6.6, TALLY_SIZE = 11;
const lane = (id, title, state) => ({ id, title, focus: "", state, waitingOn: null, since: "2026-11-17T10:00:00Z" });
const queued = (order, subject, laneId, stage) => ({ order, subject, title: subject, lane: laneId, stage, head: "a".repeat(40),
  syncedHead: null, leadInputs: [], reportedRounds: 1, runId: null, checks: [] });

/** Five stories, none landed, no finish lines: one syncing, one ready to be checked, three being built or planned. */
function record(finish = []) {
  return {
    state: {
      schemaVersion: "delivery-state/1", version: 3,
      meta: { headline: "Layout", updatedAt: NOW, baseHead: null, releasedHead: null, checking: { now: null, next: [] } },
      lanes: [lane("lane-1", "One", "idle"), lane("lane-2", "Two", "working"), lane("lane-3", "Three", "working")],
      queue: [queued(1, "APP-1", "lane-1", "syncing"), queued(2, "APP-2", "lane-2", "queued")],
      laneOwnership: { "lane-1": [], "lane-2": [], "lane-3": [] },
      planned: [
        { subject: "APP-3", title: "APP-3", lane: "lane-2", stage: "building" },
        { subject: "APP-4", title: "APP-4", lane: "lane-3", stage: "building" },
        { subject: "APP-5", title: "APP-5", lane: "lane-1", stage: "planned", after: ["APP-1"] },
      ],
      finish, followups: [], leases: [], extensions: {},
    },
    journal: [],
    publication: {},
  };
}
async function line(document) {
  const [first] = await openPage(pageWithRuns(waited(document), {}, { config: CONFIG }), { now: NOW });
  return first.app;
}

test("a stop ready to be checked sits on its line, with none of the Now page's ready block's spacing", async () => {
  let seen = null;
  await openPage(pageWithRuns(waited(record()), {}, { config: CONFIG }), { now: NOW, steps: [{ run(window) {
    const ready = window.document.querySelector('.stop-btn.ready[data-subject="APP-2"]');
    const style = ready && window.getComputedStyle(ready);
    seen = ready && { marginTop: style.marginTop, display: style.display };
  } }] });
  expect(seen === null).toBe(false);
  expect(seen.marginTop === "" || seen.marginTop === "0px").toBe(true);
  expect(seen.display === "grid").toBe(false);
});

test("the finish post's label is drawn whole inside the merge drawing", async () => {
  for (const finish of [[], [{ id: "F1", text: "It works", met: false }]]) {
    const app = await line(record(finish));
    const svg = app.querySelector("svg.line-merge");
    const postX = Number(/^M([\d.]+)/.exec(svg.querySelector(".mg-post").getAttribute("d"))[1]);
    const label = [...svg.querySelectorAll("text.mg-label")].find((node) => node.textContent === "Finish line");
    expect(Number(label.getAttribute("x"))).toBe(postX);
    expect(Number(svg.getAttribute("width")) - postX >= LABEL_HALF).toBe(true);
  }
});

test("the finish post's label never sits on the tally of what has landed", async () => {
  const app = await line(record());
  const svg = app.querySelector("svg.line-merge");
  const postX = Number(/^M([\d.]+)/.exec(svg.querySelector(".mg-post").getAttribute("d"))[1]);
  const tally = svg.querySelector("text.mg-tally");
  const label = [...svg.querySelectorAll("text.mg-label")].find((node) => node.textContent === "Finish line");
  const tallyEnd = Number(tally.getAttribute("x")) + tally.textContent.length * TALLY_CHAR;
  // The tally reaches under the label here, so the label stands clear above it.
  expect(tallyEnd > postX - LABEL_HALF).toBe(true);
  expect(Number(label.getAttribute("y")) <= Number(tally.getAttribute("y")) - TALLY_SIZE - 2).toBe(true);
});
