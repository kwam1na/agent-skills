// A scoped tracker snapshot puts another repository's children of the same parent outside the delivery. The record
// keeps them as `outside`, and the line map lists them in a collapsed "Elsewhere in KEY" fold under Parked, each with
// its tracker status and the value that places it elsewhere, linked as any ticket is. A record without them draws
// no fold.
import { test } from "node:test";

import { REPLAYED_RUNS, block, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^[A-Z]+-[0-9]+$" } };
const OUTSIDE = [
  { subject: "APP-22", title: "[Backend] Login API", state: "completed", status: "Ready for QA", marks: { component: ["Backend"] } },
  { subject: "APP-23", title: "Backend: token refresh", state: "started", status: "In QA",
    marks: { component: ["Backend"], label: ["backend", "api"] } },
];

function document(outside) {
  const shown = record({ queue: [item("APP-21", "building")], journal: journal([[10, "lane.assigned", "APP-21", { data: { title: "Login" } }]]),
    meta: { trackerParent: "APP-10" } });
  if (outside !== undefined) shown.state.outside = outside;
  return shown;
}
function open(page, scenario = {}) {
  return openPage(page, { now: NOW, store: false, ...scenario });
}

test("outside work is listed in a collapsed Elsewhere fold, with its status, its marks and its ticket's link", async () => {
  const page = pageWithRuns(document(OUTSIDE), {}, { config: CONFIG });
  expect(Object.keys(block(page, "delivery-links").issues)).toContain("APP-22");
  const [shown, opened] = await open(page, { steps: [{ toggle: "#parked .elsewhere-fold" }] });
  const fold = shown.app.querySelector("#parked .elsewhere-fold");
  expect(fold.hasAttribute("open")).toBe(false);
  expect(fold.querySelector("summary").textContent).toBe("Elsewhere in APP-10 · 2 pieces other deliveries hold");
  const rows = [...fold.querySelectorAll("li")].map((row) => row.textContent);
  expect(rows).toEqual(["APP-22[Backend] Login APIReady for QA · Backend", "APP-23Backend: token refreshIn QA · Backend · backend, api"]);
  expect(fold.querySelector('a.issue-link[data-ticket="APP-22"]').getAttribute("href")).toBe("https://tracker.example/issue/APP-22");
  // Parked's own count is the follow-ups alone: the work elsewhere was never set aside here.
  expect(shown.app.querySelector("#parked .title").textContent).toBe("Nothing parked yet.");
  expect(opened.app.querySelector("#parked .elsewhere-fold").hasAttribute("open")).toBe(true);
});

test("an opened fold stays open through a redraw, and one piece reads in the singular", async () => {
  const [, opened, pushed] = await open(pageWithRuns(document(OUTSIDE), {}, { config: CONFIG }),
    { store: { read: "pending" }, steps: [{ toggle: "#parked .elsewhere-fold" }, { push: document(OUTSIDE.slice(0, 1)) }] });
  expect(opened.app.querySelector("#parked .elsewhere-fold").hasAttribute("open")).toBe(true);
  const fold = pushed.app.querySelector("#parked .elsewhere-fold");
  expect(fold.hasAttribute("open")).toBe(true);
  expect(fold.querySelector("summary").textContent).toBe("Elsewhere in APP-10 · 1 piece another delivery holds");
});

test("a record with no outside work, or an empty list, draws no fold", async () => {
  for (const outside of [undefined, [], [{ title: "no subject" }]]) {
    const [shown] = await open(pageWithRuns(document(outside), {}, { config: CONFIG }));
    expect(shown.app.querySelector("#parked .elsewhere-fold")).toBeNull();
  }
});

test("an Elsewhere row's ticket card names the outside work's own title", async () => {
  const [, hovered] = await open(pageWithRuns(document(OUTSIDE), {}, { config: CONFIG }),
    { steps: [{ hover: '#parked .elsewhere-fold [data-ticket="APP-23"]' }] });
  const card = hovered.app.querySelector(".issue-card");
  expect(card.hidden).toBe(false);
  expect(card.textContent).toContain("Backend: token refresh");
});
