// A round's confirmed notes: each leads with what its lens found and how serious it was, from the round that raised
// it, and folds its fix beneath; a lens whose notes were all fixed is one line; any other ending stays in sight; and the
// round's sentence counts every ending. Storage starts as the page leaves it.
import { test } from "node:test";

import { block, embedded, expect, openPage, pageWithRuns } from "./support.mjs";

const A = "a".repeat(40), B = "b".repeat(40);
const TESTING = "lens.adversarial-testing", CORRECTNESS = "lens.outcome-correctness";

/** One lens's result for a round: what it raised, and what it settled of the round before. */
function entry(lens, round, candidate, findings, resolved) {
  return { lens, state: "obtained", binding: { subjectRef: "WORK-1", candidateRef: candidate, round }, result: {
    schemaVersion: "review-lens-result/1", lensId: lens, subjectRef: "WORK-1", candidateRef: candidate, round, evidence: { observed: true },
    outcome: findings.length ? "changes-requested" : "aligned", findings, resolvedFindings: resolved } };
}
const finding = (id, severity, summary) => ({ id, severity, summary, state: "open" });
const closure = (id, state, resolution) => ({ id, state, resolution, ...(state === "closed" ? { fixCommit: B } : {}) });

/** Round 1 raises three Testing notes and two Correctness ones; round 2 fixes every Testing note, and of Correctness's
 * fixes one and withdraws the other. */
function document() {
  const one = { round: 1, outcome: "unresolved", entries: [
    entry(TESTING, 1, A, [finding("T1", "P1", "A retry after a timeout charges twice"), finding("T2", "P2", "No test covers a dropped attempt."),
      finding("T3", "P3", "The log line repeats the order id.")], []),
    entry(CORRECTNESS, 1, A, [finding("C1", "P1", "The receipt keeps the first total."), finding("C2", "P2", "The banner hides the error.")], [])] };
  const two = { round: 2, outcome: "aligned", entries: [
    entry(TESTING, 2, B, [], [closure("T1", "closed", "A case now times out the first attempt and asserts one charge."),
      closure("T2", "closed", "test_dropped_attempt covers it."), closure("T3", "closed", "The id is logged once.")]),
    entry(CORRECTNESS, 2, B, [], [closure("C1", "closed", "The receipt reads the last attempt."),
      closure("C2", "withdrawn", "The banner was never shown on error.")])] };
  return { state: { lanes: [], queue: [{ subject: "WORK-1", title: "Work", stage: "merged", head: B, runId: null }] }, journal: [],
    reviewEvidence: { "WORK-1": { candidateRef: B, digest: "d".repeat(64), outcomes: [one, two] } } };
}
const config = { spec: "delivery-views-config/1", forges: [{ name: "GitHub", repository: "acme/shop",
  pullUrl: "https://github.com/{repository}/pull/{number}", commitUrl: "https://github.com/{repository}/commit/{sha}" }],
  lenses: { [TESTING]: { name: "Testing" }, [CORRECTNESS]: { name: "Correctness" } } };
const page = pageWithRuns(document(), {}, { config });
/** The same page with a finding gone from the round that raised it, as when that round's list was clipped. */
function without(id) {
  const reviews = block(page, "delivery-reviews");
  for (const summary of Object.values(reviews)) {
    for (const round of summary.roundDetail ?? []) round.raised = (round.raised ?? []).filter((entry) => entry.id !== id);
  }
  return page.replace(/(<script type="application\/json" id="delivery-reviews">)[^<]*(<\/script>)/, (_, open, close) => open + embedded(reviews) + close);
}
const groups = (app) => [...app.querySelectorAll(".run-card .run-closure-reviewer")];
const row = (fold) => ({ summary: fold.querySelector("summary .run-finding-text").textContent,
  priority: fold.querySelector("summary .run-closure-sev")?.textContent ?? null, ending: fold.querySelector("summary .run-fate").textContent });
const rows = (scope) => [...scope.querySelectorAll(".run-closures")].map(row);

test("the sentence counts every ending, naming the fixes' commit, and each confirmed note leads with what was found", async () => {
  const [shown] = await openPage(page);
  const app = shown.app;
  expect(app.querySelector(".run-card .run-settled").textContent).toBe("This round confirmed 5 earlier notes: 4 fixed in bbbbbbb, 1 withdrawn.");
  expect(app.querySelector(".run-card .run-settled a.run-commit").getAttribute("href")).toBe("https://github.com/acme/shop/commit/" + B);
  const [testing, correctness] = groups(app);
  // Correctness has an ending other than fixed: the withdrawn note is in sight under the lens, its ending named, and
  // the fixed one folds beneath a count of its own.
  expect(correctness.querySelector(".run-lens-name").textContent).toBe("Correctness");
  expect([...correctness.querySelectorAll(":scope > .run-findings .run-closures")].map(row)).toEqual([
    { summary: "The banner hides the error.", priority: "moderate", ending: "withdrawn" }]);
  const folded = correctness.querySelector(":scope > .run-closure-lens");
  expect(folded.hasAttribute("open")).toBe(false);
  expect(folded.querySelector("summary").textContent).toBe("1 fixed");
  expect(rows(folded)).toEqual([{ summary: "The receipt keeps the first total.", priority: "serious", ending: "fixed" }]);
  // Testing's notes were all fixed: one closed line, the lens and its count, and the findings beneath it worded as
  // raised, a full stop added where the summary had none.
  const whole = testing.querySelector(".run-closure-lens");
  expect(whole.hasAttribute("open")).toBe(false);
  expect(whole.querySelector(":scope > summary").textContent).toBe("Testing · 3 fixed");
  expect(testing.querySelector(":scope > .run-findings")).toBeNull();
  expect(rows(whole)).toEqual([{ summary: "A retry after a timeout charges twice.", priority: "serious", ending: "fixed" },
    { summary: "No test covers a dropped attempt.", priority: "moderate", ending: "fixed" },
    { summary: "The log line repeats the order id.", priority: "minor", ending: "fixed" }]);
  // Nothing beneath a row shows until it is opened: every row starts closed.
  expect([...app.querySelectorAll(".run-card .run-closures")].map((fold) => fold.hasAttribute("open"))).toEqual([false, false, false, false, false]);
});

test("opened, a confirmed note shows its resolution, its commit and its id", async () => {
  const [, , opened] = await openPage(page, { steps: [{ click: ".run-closure-lens > summary", index: 0 }, { click: ".run-closures > summary", index: 0 }] });
  const testing = groups(opened.app)[0];
  expect(testing.querySelector(".run-closure-lens").hasAttribute("open")).toBe(true);
  const first = testing.querySelector(".run-closures");
  expect(first.hasAttribute("open")).toBe(true);
  expect([...first.children].slice(1).map((part) => [part.className, part.textContent])).toEqual([
    ["run-closure-resolution", "A case now times out the first attempt and asserts one charge."],
    ["run-closure-evidence", "Fixed in bbbbbbb"],
    ["run-closure", "T1"]]);
  expect(first.querySelector(".run-closure-evidence a").getAttribute("href")).toBe("https://github.com/acme/shop/commit/" + B);
  // A note whose ending wasn't a fix names no fix commit.
  const withdrawn = groups(opened.app)[1].querySelector(":scope > .run-findings .run-closures");
  expect([...withdrawn.children].slice(1).map((part) => part.className)).toEqual(["run-closure-resolution", "run-closure"]);
});

test("a note whose finding isn't on the page leads with its resolution and opens on its id, as before", async () => {
  const [shown] = await openPage(without("C1"));
  const folded = groups(shown.app)[1].querySelector(":scope > .run-closure-lens");
  expect(rows(folded)).toEqual([{ summary: "The receipt reads the last attempt.", priority: null, ending: "fixed" }]);
  expect([...folded.querySelector(".run-closures").children].slice(1).map((part) => [part.className, part.textContent])).toEqual([["run-closure", "C1"]]);
  // The others still lead with their findings.
  expect(rows(groups(shown.app)[0])[0].summary).toBe("A retry after a timeout charges twice.");
});

test("each lens's fold and each note's keep their open state through a redraw", async () => {
  const [, , , pushed] = await openPage(page, { steps: [{ click: ".run-closure-lens > summary", index: 0 }, { click: ".run-closures > summary", index: 1 },
    { push: document() }] });
  const [testing, correctness] = groups(pushed.app);
  expect(testing.querySelector(".run-closure-lens").hasAttribute("open")).toBe(true);
  expect([...testing.querySelectorAll(".run-closures")].map((fold) => fold.hasAttribute("open"))).toEqual([false, true, false]);
  expect(correctness.querySelector(".run-closure-lens").hasAttribute("open")).toBe(false);
});
