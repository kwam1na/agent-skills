// A story settled with no change needed: the lead accepted the lane's evidence that its criteria already hold, so it
// never entered the queue. The line map shows it closed, with its evidence, counts it landed, and the plan counts its
// proofs done, as the close does.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, planFor, record, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const BASE = "a".repeat(40);

function render(document, plan) {
  return pageWithRuns(document, {}, { config: CONFIG, ...(plan !== undefined ? { plan } : {}) });
}
function open(page, scenario = {}) {
  return openPage(page, { now: NOW, store: false, ...scenario });
}
const landed = (subject, at = 10) => [[at, "approve", subject], [at + 1, "candidate.handed", subject], [at + 2, "candidate.merged", subject]];
const close = [40, "delivery.closed", null];
function noChange(subject, stage = "settled") {
  return { subject, title: `Work ${subject}`, lane: "lane-1", stage,
    noChange: { evidence: `${subject}.1: test_it passes at the base`, baseHead: BASE, handedAt: "2026-10-30T10:00:00Z",
      ...(stage === "settled" ? { settledAt: "2026-10-30T11:00:00Z" } : {}) } };
}

test("a story settled with no change reads closed, counts as landed at the close, and its card shows the evidence", async () => {
  const document = record({ queue: [item("A", "merged")], planned: [noChange("S")], journal: journal([...landed("A"), close]) });
  const [shown, card] = await open(render(document), { steps: [{ click: '.stop-btn[data-subject="S"]' }] });
  const stop = shown.app.querySelector('.stop-btn[data-subject="S"]');
  expect(stop.className).not.toContain("unlanded");
  expect(stop.getAttribute("aria-label")).toBe("Work S, Closed, no change needed");
  expect(shown.app.querySelector("#now .title").textContent).not.toContain("1 of 2");
  expect(shown.app.querySelector("#left .say").textContent).toBe("2 of 2 pieces have landed.");
  expect(card.app.querySelector("#detail").textContent)
    .toContain(`No change needed. Evidence, run at the main line's ${BASE.slice(0, 10)}: S.1: test_it passes at the base`);
  expect(card.app.querySelector("#detail .d-stage").textContent).toContain("Closed, no change needed");
});

test("a no-change hand-off waiting for the lead reads as handed over, not in review, and holds the count open", async () => {
  const document = record({ queue: [item("A", "merged")], planned: [noChange("W", "review")], journal: journal(landed("A")) });
  const [shown, card] = await open(render(document), { steps: [{ click: '.stop-btn[data-subject="W"]' }] });
  const stop = shown.app.querySelector('.stop-btn[data-subject="W"]');
  expect(stop.getAttribute("aria-label")).toBe("Work W, Handed over with no change needed, waiting for the lead");
  expect(card.app.querySelector("#detail").textContent).toContain("Evidence, run at the main line's");
  expect(shown.app.querySelector("#left .left-number").firstChild.textContent).toBe("1");
});

test("the plan counts every proof of a unit settled with no change done, and says it closed with no change", async () => {
  const plan = planFor([unit("A"), unit("S")], { finish: [] });
  const document = record({ queue: [item("A", "merged")], planned: [noChange("S")], journal: journal(landed("A")) });
  const [shown] = await open(render(document, plan));
  const cell = [...shown.app.querySelectorAll("#plan .plan-all .plan-unit")]
    .find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === "S");
  expect(cell.querySelector(".pu-standing").textContent).toBe("Closed, no change needed");
  expect([...cell.querySelectorAll(".plan-proof")].map((proof) => proof.className)).toEqual(
    ["plan-proof done", "plan-proof done", "plan-proof done"]);
  expect(cell.classList.contains("proven")).toBe(true);
  // While it waits for the lead, it is not yet proven.
  const waiting = record({ queue: [item("A", "merged")], planned: [noChange("S", "review")], journal: journal(landed("A")) });
  const [before] = await open(render(waiting, plan));
  const open_ = [...before.app.querySelectorAll("#plan .plan-all .plan-unit")]
    .find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === "S");
  expect(open_.querySelector(".pu-standing").textContent).not.toBe("Closed, no change needed");
  expect(open_.classList.contains("proven")).toBe(false);
});
