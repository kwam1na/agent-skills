// A journal kind the line map names: another delivery on the base merged a change to a file one of this delivery's
// open stories names.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

test("a peer delivery's merge of an open story's file reads as a peer's merge in the story", async () => {
  const summary = "FX-9, a delivery on main, merged FX-92 at aaaaaaaaaa, changing src/one.txt, which A names";
  const document = record({ queue: [item("A", "queued")], journal: journal([[10, "candidate.handed", "A"],
    [11, "peer.merged", "A", { summary }]]) });
  const [shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false });
  const labels = [...shown.app.querySelectorAll("#happened .beat-label")].map((label) => label.textContent);
  expect(labels).toContain("Another delivery landed");
  expect(shown.app.querySelector("#happened .story").textContent.includes("peer.merged")).toBe(false);
});
