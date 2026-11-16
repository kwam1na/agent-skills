// A journal kind the line map names: a fullHealth whose failing tests passed alone is flaky, never a broken base.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

test("a fullHealth whose failing tests passed alone reads as flaky in the story, not as a broken base", async () => {
  const summary = "fullHealth failed at aaaaaaaaaa after A's merge, and its failing tests passed alone: tests.x.X.test_y";
  const document = record({ queue: [item("A", "merged")], journal: journal([[10, "candidate.merged", "A"], [11, "base.flaky", null, { summary }]]) });
  const [shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false });
  // Its label is the registry's, through the story; nothing reads it as a break.
  const labels = [...shown.app.querySelectorAll("#happened .beat-label")].map((label) => label.textContent);
  expect(labels).toContain("Main line flaky");
  expect(labels.includes("Main line broke")).toBe(false);
  expect(shown.app.querySelector("#happened .setback")).toBeNull();
});
