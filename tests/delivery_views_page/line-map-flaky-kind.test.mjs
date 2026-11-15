// A journal kind the line map names: a fullHealth whose failing tests passed alone is flaky, never a broken base.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

test("a fullHealth whose failing tests passed alone reads as flaky in the feed, not as a broken base", async () => {
  const summary = "fullHealth failed at aaaaaaaaaa after A's merge, and its failing tests passed alone: tests.x.X.test_y";
  const document = record({ queue: [item("A", "merged")], journal: journal([[10, "candidate.merged", "A"], [11, "base.flaky", null, { summary }]]) });
  const [shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false });
  const feed = [...shown.app.querySelectorAll(".who")].map((who) => who.textContent);
  expect(feed.some((words) => words.startsWith("Flaky on the main line"))).toBe(true);
  expect(feed.some((words) => words.startsWith("Base broken"))).toBe(false);
});
