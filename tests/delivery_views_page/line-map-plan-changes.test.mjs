// The plan chapter's changes: a criterion `lead-plan` dropped from a story that stays reads as that criterion struck
// through, with its reason, beside a whole story's removal. The clock and time zone are fixed, and storage starts empty.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { RECORD_KINDS, finishLine, item, journal, minute, planFor, record, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  lenses: { "lens.outcome-correctness": { name: "Correctness" }, "lens.adversarial-testing": { name: "Testing" } } };

/** A and B approved with two criteria each; A drops its second criterion and gains a third, and B is removed whole. */
function delivery() {
  const lines = [finishLine("all", { criteria: "all", proofs: ["review"] })];
  const document = record({ queue: [item("A", "merged")], journal: journal([[10, "approve", "A"]]),
    finish: lines.map((line) => ({ id: line.id, text: line.text, met: false })) });
  const approved = [unit("A", { criteria: 2 }), unit("B", { criteria: 2 })];
  const plan = planFor([unit("A", { criteria: [{ id: "A.1", text: "Criterion 1 of A" }, { id: "A.3", text: "Criterion 3 of A" }] }), unit("B", { criteria: 2, removed: true })], { kinds: RECORD_KINDS, finish: lines,
    snapshot: approved,
    changes: [
      { id: "c1", at: minute(30), kind: "removed", subjects: ["A"], criteria: ["A.2"], by: "lead", why: "It moves to its own story.",
        source: "lead-plan", before: "Criterion 2 of A" },
      { id: "c2", at: minute(31), kind: "removed", subjects: ["B"], by: "lead", why: "Out of scope.", source: "lead-plan" },
      { id: "c3", at: minute(32), kind: "added", subjects: ["A"], criteria: ["A.3"], by: "lead", why: "Found in review.", source: "lead-plan",
        after: "Criterion 3 of A" }] });
  return { document, plan };
}

test("a dropped criterion is struck through with its reason, and reads apart from a removed story", async () => {
  const { document, plan } = delivery();
  const page = pageWithRuns(document, {}, { config: CONFIG, plan });
  const [, opened] = await openPage(page, { now: NOW, store: false, steps: [{ toggle: '.plan-fold[data-fold="changes"]' }] });
  const rows = [...opened.app.querySelectorAll("#plan .plan-change")];
  const shown = rows.map((row) => ({
    classes: row.className, kind: row.querySelector(".pc-kind").textContent, subjects: row.querySelector(".pc-subjects").textContent,
    struck: [...row.querySelectorAll(".redline del")].map((node) => node.textContent), added: row.querySelectorAll(".redline ins").length,
    why: row.querySelector(".pc-why").textContent }));
  expect(shown).toEqual([
    { classes: "plan-change added", kind: "Added", subjects: "A", struck: [], added: 0, why: "By lead · Found in review. · lead-plan" },
    { classes: "plan-change removed", kind: "Removed", subjects: "B", struck: [], added: 0, why: "By lead · Out of scope. · lead-plan" },
    { classes: "plan-change removed criterion", kind: "Removed criterion", subjects: "A · A.2", struck: ["Criterion 2 of A"], added: 0,
      why: "By lead · It moves to its own story. · lead-plan" }]);
  // A's redline is its criterion's wording alone, struck whole: nothing of it is kept.
  expect(rows[2].querySelector(".redline").textContent).toBe("Criterion 2 of A");
  // A stays in the plan, not removed, with the criterion it kept.
  const units = [...opened.app.querySelectorAll("#plan .plan-all .plan-unit")].map((row) =>
    [row.querySelector("[data-ticket]").getAttribute("data-ticket"), row.classList.contains("removed"),
      [...row.querySelectorAll(".pu-criteria .pu-id")].map((node) => node.textContent)]);
  expect(units.find((entry) => entry[0] === "A")).toEqual(["A", false, ["A.1", "A.3"]]);
  // The scope chart counts units: a criterion dropped or added leaves the count, and only B's removal lowers it.
  expect(opened.app.querySelector("#plan svg.plan-scope").getAttribute("aria-label")).toBe("Units in scope: 2 at approval, 1 now, over 3 changes");
});
