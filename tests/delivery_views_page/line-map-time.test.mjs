// The line map tells days apart in the reader's own time zone, so a page read just after local midnight
// calls the evening before "yesterday" even when it is the same day in UTC.
import { test } from "node:test";
import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";

process.env.TZ = "America/Los_Angeles";

test("a page read just after local midnight says yesterday for the evening before", async () => {
  const document = { state: {
    lanes: [{ id: "lane-pay", title: "Payments", state: "working" }],
    queue: [{ subject: "PAY-1", title: "Retry card payments", lane: "lane-pay", stage: "merged", order: 1 },
      { subject: "PAY-2", title: "Hold the cart", lane: "lane-pay", stage: "merged", order: 2 }],
  }, journal: [
    { at: "2026-10-22T05:00:00Z", id: 1, kind: "candidate.merged", lane: null, subject: "PAY-1", summary: "PAY-1 merged" },
    { at: "2026-10-22T06:55:00Z", id: 2, kind: "candidate.merged", lane: null, subject: "PAY-2", summary: "PAY-2 merged" },
  ] };
  const page = pageWithRuns(document, {}, { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS } });
  // 00:10 on Oct 22 in Los Angeles is 07:10 UTC; PAY-1 landed at 22:00 the evening before, local time.
  const now = "2026-10-22T07:10:00Z";
  const [, first, second] = await openPage(page, { now, steps: [
    { click: '.stop-btn[data-subject="PAY-1"]' }, { click: '.stop-btn[data-subject="PAY-2"]' }] });
  expect(first.app.querySelector("#detail .d-meta").textContent).toContain("Landed yesterday at 10:00 pm");
  expect(second.app.querySelector("#detail .d-meta").textContent).toContain("Landed 15 minutes ago");
});

test("recovered landings show source time, unknowns, and retain observation in the feed", async () => {
  const document = { state: { lanes: [], queue: [
    { subject: "PAY-1", title: "Earlier merge", stage: "merged", order: 1 },
    { subject: "PAY-2", title: "Latest merge", stage: "merged", order: 2 },
    { subject: "PAY-3", title: "Unknown merge", stage: "merged", order: 3 },
  ] }, journal: [
    { at: "2026-10-22T06:55:00Z", id: 1, kind: "candidate.merged", subject: "PAY-2", summary: "Normal merge" },
    { at: "2026-10-22T07:09:00Z", occurredAt: "2026-10-22T05:00:00Z", id: 2, kind: "candidate.merged", subject: "PAY-1", summary: "Recovered earlier merge" },
    { at: "2026-10-22T07:09:30Z", occurredAt: null, id: 3, kind: "candidate.merged", subject: "PAY-3", summary: "Recovered unknown merge" },
  ] };
  const page = pageWithRuns(document, {}, { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS } });
  const [initial, earlier, unknown] = await openPage(page, { now: "2026-10-22T07:10:00Z", steps: [
    { click: '.stop-btn[data-subject="PAY-1"]' }, { click: '.stop-btn[data-subject="PAY-3"]' }] });
  expect(initial.app.textContent).toContain("Last landed: Latest merge, 15m ago.");
  expect(earlier.app.querySelector("#detail .d-meta").textContent).toContain("Landed yesterday at 10:00 pm");
  expect(unknown.app.querySelector("#detail .d-meta").textContent).toContain("Landed · time unknown");
  expect(initial.app.querySelector('.feed time[datetime="2026-10-22T07:09:00Z"]') !== null).toEqual(true);
});
