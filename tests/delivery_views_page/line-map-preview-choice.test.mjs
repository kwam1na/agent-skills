// A plan preview after a delivery's plan: a reader's "As approved" on the delivery is never carried into the preview,
// which has one plan, the one under review, and always draws it as it stands.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { inAppDelivery, minute, previewDelivery } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  lenses: { "lens.outcome-correctness": { name: "Correctness" }, "lens.adversarial-testing": { name: "Testing" } } };

const render = (document, plan) => pageWithRuns(document, {}, { config: CONFIG, plan });
const rows = (app) => [...app.querySelectorAll("#plan .plan-all .plan-unit")]
  .map((row) => [row.querySelector("[data-ticket]").getAttribute("data-ticket"), row.classList.contains("removed")]);

test("a preview drawn after a reader chose As approved on a delivery plan still draws as amended", async () => {
  const delivery = inAppDelivery();
  // The preview's snapshot holds a story its units no longer do: drawn as approved, it would show that story, removed.
  const { document: previewDocument, plan: preview } = previewDelivery({ rounds: 1 });
  preview.approved.snapshot.units = [...preview.approved.snapshot.units,
    { subject: "SHOP-99", title: "A story the plan dropped", criteria: [], proofs: ["review", "handed", "merged"] }];
  preview.updated = "2026-10-30T13:00:00Z";
  // The record the live page follows moves on to the preview: its plan.updated names the preview's plan.
  const moved = structuredClone(previewDocument);
  moved.journal.push({ id: moved.journal.length + 1, at: minute(40), kind: "plan.updated", lane: null, subject: null,
    data: { updated: preview.updated, changed: ["units"] } });
  const snaps = await openPage(render(delivery.document, delivery.plan), {
    now: NOW, store: { read: "pending" }, quicker: 100, frames: true, rendered: render(moved, preview),
    steps: [{ toggle: '.plan-fold[data-fold="units"]' }, { click: ".plan-switch button", index: 1 }, { push: moved }, { asked: 1 }],
  });
  const chose = snaps[2].app;
  expect([...chose.querySelectorAll(".plan-switch button")].map((button) => button.getAttribute("aria-pressed")))
    .toEqual(["false", "true"]);
  const drawn = snaps[snaps.length - 1].app;
  expect(drawn.querySelector("#plan .plan-outcome").textContent).toBe(preview.outcome.summary);
  expect(drawn.querySelector(".plan-switch")).toBeNull();
  expect(rows(drawn)).toEqual(preview.units.map((unit) => [unit.subject, false]));
});
