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

test("detail times follow the subject's current stage and attempt", async () => {
  const rows = [
    ["planned", null, null], ["building", "Started", "2026-10-22T05:00:00Z"],
    ["review", "In review since", "2026-10-22T05:20:00Z"],
    ["queued", "Handed over", "2026-10-22T06:00:00Z"],
    ["checking", "Checking since", "2026-10-22T06:10:00Z"],
    ["checked", "Checked", "2026-10-22T06:15:00Z"],
    ["stopped", "Stopped", "2026-10-22T06:20:00Z"],
    ["merging", "Time unknown", null], ["merged", "Landed", "2026-10-22T06:30:00Z"],
  ];
  for (const [stage, label, expectedTime] of rows) {
    const history = [
      { kind: "review.round", at: "2026-10-22T05:30:00Z", review: { startedAt: "2026-10-22T05:20:00Z" } },
      { kind: "candidate.handed", at: "2026-10-22T06:00:00Z" },
      { kind: "check.started", at: "2026-10-22T06:10:00Z" },
      { kind: "check.finished", at: "2026-10-22T06:15:00Z" },
      { kind: "candidate.stopped", at: "2026-10-22T06:20:00Z" },
      { kind: "candidate.merged", at: "2026-10-22T07:00:00Z", occurredAt: "2026-10-22T06:30:00Z" },
    ];
    const end = { planned: 0, building: 0, review: 1, queued: 2, checking: 3, checked: 4, stopped: 5, merging: 4, merged: 6 }[stage];
    const doc = { state: { lanes: [{ id: "lane-1", state: "working", since: "2026-10-22T07:09:00Z" }],
      queue: [], planned: [{ subject: "PAY-1", title: "Payment", lane: "lane-1", stage }] },
      stats: { "PAY-1": { assignedAt: "2026-10-22T05:00:00Z" } },
      journal: history.slice(0, end).map((entry, index) => ({ ...entry, id: index + 1, subject: "PAY-1" })) };
    const [shown] = await openPage(pageWithRuns(doc, {}, { config: { spec: "delivery-views-config/1" } }), { now: "2026-10-22T07:10:00Z" });
    const meta = shown.app.querySelector("#detail .d-meta");
    const timing = meta.querySelector(".d-stage-time");
    if (label === null) { expect(timing).toBeNull(); continue; }
    expect(meta.textContent).toContain(label);
    if (expectedTime) {
      expect(timing.title).toContain(expectedTime);
      expect(timing.getAttribute("aria-label")).toContain(expectedTime);
    } else { expect(timing.title).toContain("unknown"); }
    if (stage !== "building") { expect(meta.textContent).not.toContain("Started"); }
  }
});

test("missing phase times never borrow another subject, earlier attempt or lane focus", async () => {
  for (const stage of ["building", "review", "checking", "checked", "stopped", "merged"]) {
    const doc = { state: { lanes: [{ id: "lane-1", since: "2026-10-22T07:09:00Z" }],
      planned: [{ subject: "PAY-1", title: "Payment", lane: "lane-1", stage }], queue: [] },
      journal: [
        { id: 1, subject: "PAY-1", kind: "review.round", at: "2026-10-22T05:00:00Z", review: { startedAt: "2026-10-22T04:30:00Z" } },
        { id: 2, subject: "PAY-1", kind: "check.started", at: "2026-10-22T05:10:00Z" },
        { id: 3, subject: "PAY-1", kind: "check.finished", at: "2026-10-22T05:20:00Z" },
        { id: 4, subject: "PAY-1", kind: "candidate.stopped", at: "2026-10-22T05:30:00Z" },
        { id: 5, subject: "PAY-1", kind: "candidate.merged", at: "2026-10-22T05:40:00Z" },
        { id: 6, subject: "PAY-1", kind: "candidate.handed", at: "2026-10-22T06:00:00Z" },
        { id: 7, subject: "OTHER", kind: "check.started", at: "2026-10-22T06:10:00Z" },
      ] };
    const [shown] = await openPage(pageWithRuns(doc, {}, { config: { spec: "delivery-views-config/1" } }), { now: "2026-10-22T07:10:00Z" });
    expect(shown.app.querySelector("#detail .d-meta").textContent).toContain("time unknown");
  }
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


test("building uses the current assignment after a rejected handoff, or reports it unknown", async () => {
  for (const assigned of [true, false]) {
    const doc = { state: { lanes: [], queue: [], planned: [{ subject: "PAY-1", stage: "building", title: "Payment" }] },
      stats: { "PAY-1": { assignedAt: "2026-10-22T04:00:00Z" } },
      journal: [
        { id: 1, kind: "lane.assigned", subject: "PAY-1", at: "2026-10-22T04:00:00Z" },
        { id: 2, kind: "candidate.handed", subject: "PAY-1", at: "2026-10-22T05:00:00Z" },
        { id: 3, kind: "candidate.rejected", subject: "PAY-1", at: "2026-10-22T05:10:00Z" },
        ...(assigned ? [{ id: 4, kind: "lane.assigned", subject: "PAY-1", at: "2026-10-22T06:00:00Z" }] : []),
      ] };
    const [shown] = await openPage(pageWithRuns(doc, {}, { config: { spec: "delivery-views-config/1" } }), { now: "2026-10-22T07:10:00Z" });
    const timing = shown.app.querySelector("#detail .d-stage-time");
    expect(timing.title).toBe("Recorded work assignment: " + (assigned ? "2026-10-22T06:00:00Z" : "unknown"));
  }
});
