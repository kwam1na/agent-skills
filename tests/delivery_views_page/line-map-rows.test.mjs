// Each lane's row says how old its words are, marks them once they may be out of date, and says when it can't tell.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";

const NOW = "2026-11-03T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

function document(lanes) {
  return { state: { meta: { updatedAt: NOW }, lanes, queue: [], planned: [] }, journal: [], publication: {} };
}
const lane = (extra = {}) => ({ id: "lane-1", title: "Lane one", focus: "Building the thing", state: "working", waitingOn: null, since: "2026-11-03T06:00:00Z", ...extra });
const minutesAgo = (count) => new Date(Date.parse(NOW) - count * 60000).toISOString();
const first = async (lanes, scenario = { store: false }) => (await openPage(pageWithRuns(document(lanes), {}, { config: CONFIG }), { now: NOW, ...scenario }))[0].app;
const row = (app) => app.querySelector(".person");
const age = (app) => row(app).querySelector(".said");

test("a fresh row says how old its words are, unmarked", async () => {
  const app = await first([lane({ focusAt: minutesAgo(12) })]);
  expect(age(app).textContent).toBe("Said 12m ago");
  expect(age(app).classList.contains("old")).toBe(false);
});

test("a row under a minute old says so", async () => {
  expect(age(await first([lane({ focusAt: minutesAgo(0) })])).textContent).toBe("Said just now");
});

test("a row past the threshold is marked, and says what that means", async () => {
  const app = await first([lane({ focusAt: minutesAgo(61) })]);
  expect(age(app).textContent).toBe("Said 1h 1m ago; may be out of date");
  expect(age(app).classList.contains("old")).toBe(true);
});

test("a row exactly at the threshold is not yet marked", async () => {
  expect(age(await first([lane({ focusAt: minutesAgo(60) })])).classList.contains("old")).toBe(false);
});

test("a row with words and no time says so rather than reading as fresh", async () => {
  const app = await first([lane()]);
  expect(age(app).textContent).toBe("No time recorded");
  expect(age(app).classList.contains("old")).toBe(false);
});

test("a time that can't be read, or is in the future, says no time rather than a wrong age", async () => {
  for (const focusAt of ["not a time", 12, null, "2026-11-03T13:00:00Z"]) {
    expect(age(await first([lane({ focusAt })])).textContent).toBe("No time recorded");
  }
});

test("a resting lane shows its age but isn't marked: an old 'merged' is still true", async () => {
  for (const state of ["idle", "done"]) {
    const app = await first([lane({ state, focus: "WORK-1 merged", focusAt: minutesAgo(300) })]);
    expect(age(app).textContent).toBe("Said 5h ago");
    expect(age(app).classList.contains("old")).toBe(false);
  }
});

test("a row with nothing said shows no age", async () => {
  expect(age(await first([lane({ focus: "", focusAt: minutesAgo(5) })]))).toBeNull();
});

test("words the record derives for a lane with no focus of its own show no age", async () => {
  const doc = { ...document([lane({ focus: "" })]), focus: { "lane-1": "queued, rounds unknown" } };
  const app = (await openPage(pageWithRuns(doc, {}, { config: CONFIG }), { now: NOW, store: false }))[0].app;
  expect(row(app).querySelector(".doing").textContent).toBe("queued, rounds unknown");
  expect(age(app)).toBeNull();
});

test("the record's words render as text, and the row still draws with fields missing", async () => {
  const app = await first([{ id: "lane-1" }, lane({ id: "lane-2", focus: "<b>Hi</b>", focusAt: minutesAgo(1) })]);
  expect(app.querySelectorAll(".person")).toHaveLength(2);
  expect(app.querySelectorAll(".person b")).toHaveLength(0);
});

test("a live page shows the same, and a push moves it", async () => {
  const page = pageWithRuns(document([lane({ focusAt: minutesAgo(90) })]), {}, { config: CONFIG });
  const snaps = await openPage(page, { now: NOW, steps: [{ push: document([lane({ focus: "Merged", state: "idle", focusAt: minutesAgo(0) })]) }] });
  expect(age(snaps[0].app).classList.contains("old")).toBe(true);
  expect(age(snaps[1].app).textContent).toBe("Said just now");
  expect(age(snaps[1].app).classList.contains("old")).toBe(false);
});
