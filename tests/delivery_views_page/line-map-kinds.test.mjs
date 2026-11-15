// A journal kind the line map names: a base re-run that was stopped, not failed, reads as stopped in the feed and in
// the piece's merge check, never as a verdict.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
// A fullHealth whose failing tests passed alone is flaky: it reads so in the feed, never as a broken base.
test("a fullHealth whose failing tests passed alone reads as flaky in the feed, not as a broken base", async () => {
  const summary = "fullHealth failed at aaaaaaaaaa after A's merge, and its failing tests passed alone: tests.x.X.test_y";
  const document = record({ queue: [item("A", "merged")], journal: journal([[10, "candidate.merged", "A"], [11, "base.flaky", null, { summary }]]) });
  const [shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false });
  const feed = [...shown.app.querySelectorAll(".who")].map((who) => who.textContent);
  expect(feed.some((words) => words.startsWith("Flaky on the main line"))).toBe(true);
  expect(feed.some((words) => words.startsWith("Base broken"))).toBe(false);
});

test("a stopped base rerun reads as stopped in the feed and in the piece's merge check", async () => {
  const summary = "Base re-run on main at aaaaaaaaaa after 4s was stopped before its verdict: stop exited 143";
  const document = record({
    queue: [item("A", "queued")],
    journal: journal([[10, "candidate.handed", "A"], [11, "check.started", "A"], [12, "check.failed", "A"],
      [13, "base.rerun.started", "A"], [14, "base.rerun.stopped", "A", { summary }]]),
  });
  const [shown, card] = await openPage(pageWithRuns(document, {}, { config: CONFIG }),
    { now: NOW, store: false, steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  const feed = [...shown.app.querySelectorAll(".who")].map((who) => who.textContent);
  expect(feed).toContain("Rerun stopped · Lane one · A");
  expect(feed.some((words) => words.startsWith("Rerun verdict"))).toBe(false);
  const history = card.app.querySelector("#detail .mh");
  expect(history.querySelector("summary .mh-sub").textContent).toMatch(/^ · Rerun stopped /);
  expect([...history.querySelectorAll("li span")].map((row) => row.textContent).at(-1)).toBe(summary);
});
