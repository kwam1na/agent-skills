// A story settled with no change needed: the lead accepted the lane's evidence that its criteria already hold, so it
// never entered the queue. The line map shows it closed, with its evidence, counts it landed, and the plan counts its
// proofs done, as the close does.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, renderPage } from "./support.mjs";
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

test("on an open delivery a settled story counts as landed everywhere the line map counts", async () => {
  const document = record({ queue: [item("A", "merged")], planned: [noChange("S")], journal: journal(landed("A")) });
  const [shown, card] = await open(render(document), { steps: [{ click: '.stop-btn[data-subject="S"]' }] });
  expect(shown.app.querySelector("#now .title").textContent).toBe("Every piece has landed.");
  expect(shown.app.querySelector("#left .left-number").firstChild.textContent).toBe("0");
  expect([...shown.app.querySelectorAll("#line .line .name")].map((name) => name.textContent)).toEqual(["Lane one2 of 2 landed"]);
  expect(shown.app.querySelector("svg[aria-label^='Every lane merges']").getAttribute("aria-label")).toContain("2 of 2 pieces landed");
  expect([...shown.app.querySelectorAll("#left .dots i")].map((dot) => dot.className)).toEqual(["", ""]);
  // The rule between the merged stop and the settled one is drawn as both landed.
  const segs = [...shown.app.querySelectorAll("#line .line .stops .seg")];
  expect(segs.map((seg) => seg.querySelector(".stop-btn").getAttribute("data-subject"))).toEqual(["A", "S"]);
  expect(segs[0].classList.contains("link-right")).toBe(true);
  expect(segs[1].classList.contains("link-left")).toBe(true);
  // Its stop is drawn landed, and its card dates it from the lead's acceptance, not the hand-off.
  expect(shown.app.querySelector('.stop-btn[data-subject="S"]').className.trim()).toBe("stop-btn merged");
  expect(card.app.querySelector("#detail .d-stage-time").getAttribute("aria-label")).toContain("2026-10-30T11:00:00Z");
  expect(card.app.querySelector("#detail .d-stage-time").getAttribute("aria-label")).not.toContain("2026-10-30T10:00:00Z");
});

test("a close that stopped partway with only settled planned work left meets the close condition", async () => {
  const document = record({ queue: [item("A", "merged")], planned: [noChange("S")], journal: journal([...landed("A"), close]),
    finish: [{ id: "line", text: "The line is met", met: true }] });
  document.state.lanes[0].state = "idle";
  const closed = document.journal.at(-1);
  document.state.meta.closing = { started: { entry: closed.id, at: closed.at } };
  const [shown] = await openPage(renderPage(document, { config: CONFIG }), { now: NOW, store: false });
  expect(shown.app.querySelector("#now .close-partway p").textContent).toContain("Finish it with lead-run --resume.");
});

test("a lane's rule is solid up to its settled stop when work after it is still to come", async () => {
  const document = record({ queue: [item("A", "merged")], journal: journal(landed("A")),
    planned: [noChange("S"), { subject: "B", title: "Work B", lane: "lane-1", stage: "planned" }] });
  const [shown] = await open(render(document));
  const stops = shown.app.querySelector("#line .line .stops");
  // Three stops, the last still to come: the rule runs solid through A and S, the second, and fades before B.
  const solid = parseFloat(stops.style.getPropertyValue("--solid"));
  const onlyA = record({ queue: [item("A", "merged")], journal: journal(landed("A")),
    planned: [{ subject: "S", title: "Work S", lane: "lane-1", stage: "building" }, { subject: "B", title: "Work B", lane: "lane-1", stage: "planned" }] });
  const [before] = await open(render(onlyA));
  const solidBefore = parseFloat(before.app.querySelector("#line .line .stops").style.getPropertyValue("--solid"));
  expect(solid).toBeGreaterThan(solidBefore);
  expect(shown.app.querySelector("#line .line .name").textContent).toBe("Lane one2 of 3 landed");
});
