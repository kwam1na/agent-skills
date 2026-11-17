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

/**
 * This delivery's own shape: approved with 4 stories, now 8, after five stories added and one removed, with a
 * criterion added, one dropped and one relaxed, one finish line raised and one added since approval.
 */
function amended() {
  const approvedLines = [finishLine("cart", { criteria: ["A.1", "B.1"], proofs: ["merged"] }, { label: "Cart" })];
  const lines = [finishLine("cart", { criteria: "all", proofs: ["merged", "review"] }, { label: "Cart", at: minute(100) }),
    finishLine("checked", { criteria: [], proofs: ["review"] }, { label: "Checked", at: minute(110) })];
  const document = record({ queue: [item("A", "merged")], journal: journal([[10, "approve", "A"]]),
    finish: lines.map((line) => ({ id: line.id, text: line.text, met: false })) });
  const approved = ["A", "B", "C", "D"].map((subject) => unit(subject, { criteria: 2 }));
  const now = [unit("A", { criteria: [{ id: "A.1", text: "Criterion 1 of A" }, { id: "A.3", text: "Criterion 3 of A" }] }),
    ...["B", "C"].map((subject) => unit(subject, { criteria: 2 })), unit("D", { criteria: 2, removed: true }),
    ...["E", "F", "G", "H", "I"].map((subject) => unit(subject))];
  const change = (id, at, kind, subjects, extra = {}) => ({ id, at: minute(at), kind, subjects, by: "lead", why: `Why ${id}.`, ...extra });
  const plan = planFor(now, { kinds: RECORD_KINDS, finish: lines, snapshot: approved, changes: [
    change("c1", 30, "added", ["E", "F"]), change("c2", 40, "added", ["G"]), change("c3", 50, "added", ["H", "I"]),
    change("c4", 60, "removed", ["D"]), change("c5", 70, "added", ["A"], { criteria: ["A.3"], after: "Criterion 3 of A" }),
    change("c6", 80, "removed", ["A"], { criteria: ["A.2"], before: "Criterion 2 of A" }),
    change("c7", 90, "relaxed", ["B"], { criteria: ["B.1"], before: "Criterion 1 of B", after: "Criterion 1 of B, eased" }),
    change("c8", 100, "finish", [], { before: approvedLines[0].covers, after: lines[0].covers }),
    change("c9", 110, "finish", [], { after: lines[1].covers })] });
  plan.approved.snapshot.finish = approvedLines;
  return { document, plan };
}
async function opened(document, plan) {
  const page = pageWithRuns(document, {}, { config: CONFIG, plan });
  const [, shown] = await openPage(page, { now: NOW, store: false, steps: [{ toggle: '.plan-fold[data-fold="changes"]' }] });
  return shown.app;
}
const scoped = (app) => app.querySelector("#plan .plan-changes .plan-scoped").textContent;
const moved = (app) => [...app.querySelectorAll("#left .plan-meter")].map((meter) =>
  [meter.querySelector(".pm-label").textContent, [...meter.querySelectorAll(".pm-moved")].map((node) => node.textContent)]);
const unreviewed = (app) => [...app.querySelectorAll("#plan .plan-change")].map((row) =>
  row.querySelector(".pc-unreviewed") ? row.querySelector(".pc-unreviewed").textContent : null);

test("the scope since approval is one sentence counted from the changes, and an unchanged plan says it is as approved", async () => {
  const { document, plan } = amended();
  expect(scoped(await opened(document, plan))).toBe("Approved with 4 stories, now 8: 5 added and 1 removed; 1 criterion added, 1 removed and 1 changed.");
  // Criteria alone leave the story count where it was, and a finish line's change is not the scope's.
  const criteria = structuredClone(plan);
  criteria.changes = plan.changes.filter((entry) => ["c6", "c7", "c8"].includes(entry.id));
  expect(scoped(await opened(document, criteria))).toBe("Approved with 4 stories, still 4; 1 criterion removed and 1 changed.");
  const untouched = structuredClone(plan);
  untouched.changes = [];
  untouched.units = untouched.approved.snapshot.units;
  untouched.finish = untouched.approved.snapshot.finish;
  const app = await opened(document, untouched);
  expect(scoped(app)).toBe("The scope is as approved: 4 stories.");
  // The record's second line isn't in an unchanged plan: its meter is the record's alone, and says nothing moved.
  expect(moved(app)).toEqual([["Cart", []], ["The checked line is met", []]]);
});

test("a finish line raised since approval says so, from what to what and when, and one added since says that", async () => {
  const { document, plan } = amended();
  const app = await opened(document, plan);
  expect(moved(app)).toEqual([
    ["Cart", ["Raised at 7:40 am: from A.1, B.1 (merged) to every criterion (merged, review)."]],
    ["Checked", ["Added since approval at 7:50 am."]]]);
  // Narrowed, it reads as lowered; neither wider nor narrower, as changed; as approved, it says nothing.
  const lowered = structuredClone(plan);
  [lowered.finish[0].covers, lowered.approved.snapshot.finish[0].covers] = [plan.approved.snapshot.finish[0].covers, plan.finish[0].covers];
  expect(moved(await opened(document, lowered))[0]).toEqual(["Cart", ["Lowered at 7:40 am: from every criterion (merged, review) to A.1, B.1 (merged)."]]);
  const sideways = structuredClone(plan);
  sideways.finish[0].covers = { criteria: ["C.1"], proofs: ["review"] };
  expect(moved(await opened(document, sideways))[0]).toEqual(["Cart", ["Changed at 7:40 am: from A.1, B.1 (merged) to C.1 (review)."]]);
});

test("every change made after approval is marked as not reviewed with the plan, and one made at approval is not", async () => {
  const { document, plan } = amended();
  expect(unreviewed(await opened(document, plan))).toEqual(Array(9).fill("Not reviewed with the plan"));
  const atApproval = structuredClone(plan);
  atApproval.changes[0].at = atApproval.approved.at;
  expect(unreviewed(await opened(document, atApproval))).toEqual([...Array(8).fill("Not reviewed with the plan"), null]);
});
