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

test("the chapter tab holds every chapter's name in one cell, so stepping between pages never changes its width", async () => {
  let seen = null;
  await openPage(pageWithRuns(waited(record()), {}, { config: CONFIG }), { now: NOW, steps: [{ run(window) {
    window.location.hash = "#left";
    window.dispatchEvent(new window.HashChangeEvent("hashchange"));
    const label = window.document.querySelector(".chapter-tab-label");
    const chapters = [...window.document.querySelectorAll(".chapter-tab-list a")].map((link) => link.textContent.replace(/^\d+/, ""));
    seen = { name: label.querySelector(".chapter-tab-name").textContent, sizers: [...label.querySelectorAll(".chapter-tab-sizer")]
      .map((sizer) => [sizer.textContent, sizer.getAttribute("aria-hidden")]), chapters,
      aria: window.document.querySelector(".chapter-tab-button").getAttribute("aria-label") };
  } }] });
  expect(seen.name).toBe("What's left");
  expect(seen.aria).toBe("Chapters: What's left");
  expect(seen.sizers).toEqual(seen.chapters.map((name) => [name, "true"]));
});

test("a long name on Now is cut to three lines and shown whole on the page's card when pointed at", async () => {
  const name = "Several engineers run one delivery together, with shared context, co-authored plans, joint approval, scoped lanes and handovers that survive a machine going away";
  const document = record();
  document.state.meta.headline = name;
  let seen = null;
  await openPage(pageWithRuns(waited(document), {}, { config: CONFIG }), { now: NOW, steps: [{ run(window) {
    const title = window.document.querySelector("#now h1.title");
    const style = window.getComputedStyle(title);
    // jsdom lays nothing out, so the name reads as cut: its full height past the clamped one.
    Object.defineProperty(title, "scrollHeight", { configurable: true, value: 150 });
    Object.defineProperty(title, "clientHeight", { configurable: true, value: 93 });
    title.dispatchEvent(new window.MouseEvent("mouseenter"));
    const card = window.document.querySelector(".issue-card");
    seen = { text: title.textContent, clamp: style.getPropertyValue("-webkit-line-clamp"), overflow: style.overflow,
      card: card.hidden ? null : card.textContent };
  } }] });
  // The whole name stays in the heading, for a screen reader; the page cuts it to three lines and its card shows it.
  expect(seen.text).toBe(name);
  expect([seen.clamp, seen.overflow]).toEqual(["3", "hidden"]);
  expect(seen.card).toBe(name);
});

test("a lane's \"+N more\" marker takes its own slot on the line, after its last stop, rather than sitting on that stop", async () => {
  const document = record();
  // Lane 2 gets four open stories: three stops are drawn and the fourth folds into "+1".
  document.state.planned.push({ subject: "APP-6", title: "APP-6", lane: "lane-2", stage: "planned" },
    { subject: "APP-7", title: "APP-7", lane: "lane-2", stage: "planned" });
  let seen = null;
  await openPage(pageWithRuns(waited(document), {}, { config: CONFIG }), { now: NOW, steps: [{ run(window) {
    const more = window.document.querySelector(".seg.more");
    const stops = more && more.parentElement;
    const style = more && window.getComputedStyle(more);
    seen = more && { text: more.textContent, last: stops.lastElementChild === more,
      before: more.previousElementSibling.querySelector(".stop-btn") !== null,
      position: style.position, flex: style.flex || style.getPropertyValue("flex-basis"), place: style.placeItems,
      paddingTop: style.paddingTop, top: style.top };
  } }] });
  expect(seen === null).toBe(false);
  expect([seen.text, seen.last, seen.before]).toEqual(["+1", true, true]);
  // An absolutely placed marker has no box of its own on the line: with no left it falls onto the last stop's label.
  expect(seen.position).toBe("relative");
  expect(seen.top === "" || seen.top === "auto").toBe(true);
  // In the flow it is a slot like a stop's, its count where a stop's mark would sit.
  expect(seen.place).toBe("start center");
  expect(seen.paddingTop).toBe("14px");
});
