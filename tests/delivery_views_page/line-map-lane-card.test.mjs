// A lane card says what its lane's next story waits on once: the story, then a "Waiting on" row of one pill per
// blocker, nearest to landing first, each in its lane's colour, filled when it is in the merge queue, with its phase in
// one word and a lock when it holds files the next story needs; one legend names those files. Once the lane's own story
// is handed off, the card follows it through the queue. Landed blockers drop off, and the block goes once the next
// story starts.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, waited } from "./support.mjs";

const NOW = "2026-11-17T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const HEAD = "42d83a4" + "0".repeat(33);
const PATH = "src/reads/ratchet.ts";
const lane = (id, title, state, focus = "") => ({ id, title, focus, focusAt: focus ? "2026-11-17T11:00:00Z" : undefined, state,
  waitingOn: null, since: "2026-11-17T10:00:00Z" });
const queued = (order, subject, title, laneId, stage, rest = {}) => ({ order, subject, title, lane: laneId, stage, head: HEAD,
  syncedHead: null, leadInputs: [], reportedRounds: 3, runId: null, checks: [], ...rest });

/**
 * Lane 1 handed APP-1 off after three rounds, and it is syncing; its next story, APP-5, comes after APP-1, APP-2 and
 * APP-3, and names a file lanes 3 and 4 both hold: APP-2 is queued in lane 3, APP-3 is being built in lane 4.
 */
function record() {
  return {
    state: {
      schemaVersion: "delivery-state/1", version: 3,
      meta: { headline: "Lane cards", updatedAt: NOW, baseHead: null, releasedHead: null, checking: { now: null, next: [] } },
      lanes: [lane("lane-1", "Pages", "idle", "APP-1 review round 3"), lane("lane-3", "Notices", "idle"), lane("lane-4", "Ratchet", "working", "the ratchet")],
      queue: [queued(1, "APP-1", "Move the commerce pages", "lane-1", "syncing"),
        queued(2, "APP-2", "Fold in the notice", "lane-3", "queued", { files: [PATH] })],
      laneOwnership: { "lane-1": [], "lane-3": ["src/reads/*"], "lane-4": ["src/reads/*"] },
      planned: [
        { subject: "APP-3", title: "Tighten the ratchet", lane: "lane-4", stage: "building", files: [PATH] },
        { subject: "APP-5", title: "Move the last pages", lane: "lane-1", stage: "planned", after: ["APP-1", "APP-2", "APP-3"], files: [PATH] },
      ],
      finish: [], followups: [], leases: [], extensions: {},
    },
    journal: [
      ...[1, 2, 3].map((round) => ({ id: round, at: `2026-11-17T11:${10 * round}:00Z`, kind: "review.round", lane: "lane-1", subject: "APP-1",
        data: { round } })),
      { id: 4, at: "2026-11-17T11:56:00Z", kind: "candidate.handed", lane: "lane-1", subject: "APP-1", data: { head: HEAD } },
    ],
    publication: {},
  };
}
const person = (app, title) => [...app.querySelectorAll(".person")].find((row) => row.querySelector("h3").textContent === title);
const pills = (card) => [...card.querySelectorAll(".next .dep")];

test("a lane's next story is named once, with one pill per blocker, nearest to landing first", async () => {
  const [first] = await openPage(pageWithRuns(waited(record()), {}, { config: CONFIG }), { now: NOW });
  const card = person(first.app, "Pages");
  expect(card.textContent.includes("is next on")).toBe(false);
  expect(card.querySelectorAll(".wait")).toHaveLength(0);
  const head = card.querySelector(".next .next-head");
  expect(head.textContent).toBe("Next APP-5 · Move the last pages");
  expect(head.querySelector(".issue-link").getAttribute("data-ticket")).toBe("APP-5");
  expect(card.querySelector(".next .waiting-on .k").textContent).toBe("Waiting on");
  const row = pills(card);
  expect(row.map((pill) => pill.querySelector(".issue-link").getAttribute("data-ticket"))).toEqual(["APP-1", "APP-2", "APP-3"]);
  expect(row.map((pill) => pill.querySelector(".phase").textContent)).toEqual(["syncing", "queued", "building"]);
  expect(row.map((pill) => pill.classList.contains("queued"))).toEqual([true, true, false]);
  expect(row.map((pill) => pill.style.getPropertyValue("--c"))).toEqual(["var(--l1)", "var(--l3)", "var(--l4)"]);
  expect(row.map((pill) => Boolean(pill.querySelector(".lock")))).toEqual([false, true, true]);
  expect(row.map((pill) => pill.getAttribute("title"))).toEqual([
    "Pages · in the merge queue",
    `Notices · in the merge queue · holds ${PATH}`,
    `Ratchet · still with its lane · holds ${PATH}`,
  ]);
  const legend = card.querySelector(".next .legend");
  expect([...legend.children].map((item) => item.textContent)).toEqual([
    "in the merge queue", "still with its lane", `holds ${PATH}, which APP-5 needs`]);
  expect(legend.textContent.split(PATH).length - 1).toBe(1);
});

test("once the lane's story is handed off, its card follows it through the merge queue", async () => {
  const [first] = await openPage(pageWithRuns(waited(record()), {}, { config: CONFIG }), { now: NOW });
  const card = person(first.app, "Pages");
  expect(card.querySelector(".doing").textContent).toBe("APP-1 · handed off at 42d83a4 after review round 3");
  expect(card.querySelector(".doing .issue-link").getAttribute("data-ticket")).toBe("APP-1");
  const chip = card.querySelector(".st .chip");
  expect(chip.textContent).toBe("In merge queue");
  expect(chip.classList.contains("queue")).toBe(true);
  expect(card.querySelector(".st .on").textContent).toBe("syncing · 4m");
  expect(card.textContent.includes("Standing by")).toBe(false);
  // A lane with nothing handed off still stands by.
  expect(person(first.app, "Notices").querySelector(".st .chip").textContent).toBe("In merge queue");
  const quiet = record();
  quiet.state.queue.splice(1, 1);
  quiet.state.planned.unshift({ subject: "APP-2", title: "Fold in the notice", lane: "lane-3", stage: "planned" });
  const [still] = await openPage(pageWithRuns(waited(quiet), {}, { config: CONFIG }), { now: NOW });
  expect(person(still.app, "Notices").querySelector(".st .chip").textContent).toBe("Standing by");
});

test("a landed blocker drops off the row, and the block goes once the next story starts", async () => {
  const landed = record();
  landed.state.queue[0].stage = "merged";
  const started = JSON.parse(JSON.stringify(landed));
  started.state.queue[1].stage = "merged";
  started.state.planned[0].stage = "settled";
  started.state.planned[1].stage = "building";
  started.state.lanes[0] = lane("lane-1", "Pages", "working", "the last pages");
  const snaps = await openPage(pageWithRuns(waited(record()), {}, { config: CONFIG }), { now: NOW,
    steps: [{ push: waited(landed) }, { push: waited(started) }] });
  const after = person(snaps[1].app, "Pages");
  expect(pills(after).map((pill) => pill.querySelector(".issue-link").getAttribute("data-ticket"))).toEqual(["APP-2", "APP-3"]);
  expect(after.querySelector(".st .chip").textContent).toBe("Standing by");
  expect(after.querySelector(".doing").textContent).toBe("APP-1 review round 3");
  const going = person(snaps[2].app, "Pages");
  expect(going.querySelectorAll(".next")).toHaveLength(0);
  expect(going.querySelector(".st .chip").textContent).toBe("Building");
});

test("a wait the record gives without its blocker is said in its own sentence, as before", async () => {
  const document = { ...record(), waits: [
    { subject: "lane-1", on: "APP-5", cause: "order", stage: "planned", frees: "landed", needsSomeone: false,
      words: "Lane 1 is next on APP-5, which waits for APP-1 to land." },
    { subject: "lane-1", on: "APP-5", cause: "claim", stage: "planned", frees: "landed", needsSomeone: false,
      words: "Lane 1 is next on APP-5, which waits for delivery PEER-1, which claims src/reads/ratchet.ts." }] };
  const [first] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW });
  const card = person(first.app, "Pages");
  expect([...card.querySelectorAll(".wait")].map((line) => line.textContent)).toEqual(document.waits.map((wait) => wait.words));
  expect(card.querySelectorAll(".next")).toHaveLength(0);
});
