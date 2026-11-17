// A finished round's findings fit their card. A band heading says the fate its findings share once ("Moderate · 4 ·
// all deferred", "Minor · deferred"), and a finding says its own fate only when it differs. There is no marks column:
// a row is its text across the card, clamped at three lines, and a deferral's follow-up is one faint line under it,
// linked when the page can address it. A round whose findings are all fixed or all open says so once, in its footer.
process.env.TZ = "UTC";

import { test } from "node:test";
import { JSDOM } from "jsdom";

import { expect, openPage, pageWithRuns } from "./support.mjs";

const A = "a".repeat(40), B = "b".repeat(40);
const OC = "lens.outcome-correctness", AT = "lens.adversarial-testing";
const CONFIG = { spec: "delivery-views-config/1", tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^WORK-[0-9]+$" } };

const finding = (id, severity, summary, extra = {}) => ({ id, severity, summary, state: "deferred", deferredIssue: `Follow up on ${id}.`, ...extra });
const entry = (lens, candidate, round, outcome, findings, resolvedFindings = []) => ({
  lens, state: "obtained", binding: { subjectRef: "WORK-1", candidateRef: candidate, round }, result: {
    schemaVersion: "review-lens-result/1", lensId: lens, subjectRef: "WORK-1", candidateRef: candidate, round, evidence: { observed: true },
    outcome, findings, resolvedFindings } });

/** WORK-1 landed at `head`, its review the `outcomes` given; WORK-9 is a planned story, so the page can link its key. */
function record(outcomes, head) {
  return { state: { lanes: [], queue: [{ subject: "WORK-1", title: "Work", stage: "merged", head, runId: null }],
                    planned: [{ subject: "WORK-9", title: "Follow-up", lane: "lane-1", stage: "planned" }] },
           journal: [], reviewEvidence: { "WORK-1": { candidateRef: head, digest: "d".repeat(64), outcomes } } };
}
const bands = (card) => [...card.querySelectorAll(".run-band")].map((band) => band.textContent);
const rows = (card) => [...card.querySelectorAll("li.run-finding")].map((row) => [
  row.querySelector(".run-finding-text").textContent, row.querySelector(".run-fate")?.textContent ?? null,
  row.querySelector(".run-followup")?.textContent ?? null]);
async function card(outcomes, head, steps = []) {
  const snaps = await openPage(pageWithRuns(record(outcomes, head), {}, { config: CONFIG }), { steps: [{ click: '[data-subject="WORK-1"]' }, ...steps] });
  return snaps.at(-1).app.querySelector("#detail .run-card");
}

test("a band says the fate its findings share once, and its rows carry no marks column", async () => {
  const shown = await card([{ round: 1, outcome: "aligned", entries: [
    entry(AT, A, 1, "aligned", [finding("at-1", "P2", "One."), finding("at-2", "P2", "Two."), finding("at-3", "P2", "Three."),
                                finding("at-4", "P2", "Four."), finding("at-5", "P3", "Five."), finding("at-6", "P3", "Six.")]),
    entry(OC, A, 1, "aligned", [finding("oc-1", "P3", "Seven.")])] }], A);
  expect(bands(shown)).toEqual(["Moderate · 4 · all deferred", "Minor · 2 · all deferred", "Minor · deferred"]);
  expect(rows(shown).map(([said, fate]) => [said, fate])).toEqual([["One.", null], ["Two.", null], ["Three.", null], ["Four.", null],
                                                                   ["Five.", null], ["Six.", null], ["Seven.", null]]);
  expect(shown.querySelectorAll(".run-marks, .run-filed").length).toBe(0);
  // The round's chip is as it was.
  expect(shown.querySelector(".run-card-outcome").textContent).toBe("Approved with 7 deferrable notes");
  // Each deferral's follow-up is one line under its finding.
  expect(rows(shown)[0][2]).toBe("↳ Follow up on at-1.");
});

test("a finding whose fate differs from its band's says its own, after its text", async () => {
  const shown = await card([
    { round: 1, outcome: "unresolved", entries: [entry(AT, A, 1, "changes-requested", [
      finding("at-1", "P2", "Fixed later", { state: "open", deferredIssue: undefined }),
      finding("at-2", "P2", "Still open", { state: "open", deferredIssue: undefined }),
      finding("at-3", "P2", "Left deferred")])] },
    { round: 2, outcome: "unresolved", entries: [entry(AT, B, 2, "aligned", [],
      [{ id: "at-1", state: "closed", fixCommit: B, resolution: "Fixed." }])] }],
    B, [{ click: ".run-pick", index: 0 }]);
  // Three fates in one band: the band says none, and each row its own, in the fate colours.
  expect(bands(shown)).toEqual(["Moderate · 3"]);
  expect(rows(shown)).toEqual([["Fixed later.", "✓ fixed", null], ["Still open.", "open", null],
                               ["Left deferred.", "deferred", "↳ Follow up on at-3."]]);
  expect([...shown.querySelectorAll("li.run-finding .run-fate")].map((fate) => fate.className))
    .toEqual(["run-fate is-fixed", "run-fate is-open", "run-fate is-deferred"]);
});

test("a round whose findings are all fixed says so once below them, and a fixed deferral shows no follow-up", async () => {
  const shown = await card([
    { round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [finding("at-1", "P3", "One."), finding("at-2", "P2", "Two.")])] },
    { round: 2, outcome: "aligned", entries: [entry(AT, B, 2, "aligned", [],
      [{ id: "at-1", state: "closed", fixCommit: B, resolution: "Fixed." }, { id: "at-2", state: "closed", fixCommit: B, resolution: "Fixed." }])] }],
    B, [{ click: ".run-pick", index: 0 }]);
  expect(bands(shown)).toEqual(["Moderate", "Minor"]);
  expect(rows(shown)).toEqual([["Two.", null, null], ["One.", null, null]]);
  expect(shown.querySelector(".run-settled").textContent).toBe("All 2 of this round's notes were fixed in bbbbbbb, and round 2 confirmed them.");
});

test("a follow-up that is a ticket the page knows, or an address, is linked; anything else stays text", async () => {
  const shown = await card([{ round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [
    finding("at-1", "P2", "Ticket.", { deferredIssue: "WORK-9" }), finding("at-2", "P2", "Address.", { deferredIssue: "https://example.test/issues/9" }),
    finding("at-3", "P2", "Unknown key.", { deferredIssue: "OTHER-4" }), finding("at-4", "P2", "Script.", { deferredIssue: "javascript:alert(1)" })])] }], A);
  const links = [...shown.querySelectorAll(".run-followup")].map((line) => line.querySelector("a")?.getAttribute("href") ?? null);
  expect(links).toEqual(["https://tracker.example/issue/WORK-9", "https://example.test/issues/9", null, null]);
  expect([...shown.querySelectorAll(".run-followup a")].every((link) => link.getAttribute("rel") === "noopener noreferrer")).toBe(true);
  expect(rows(shown).map((row) => row[2])).toEqual(["↳ WORK-9", "↳ https://example.test/issues/9", "↳ OTHER-4", "↳ javascript:alert(1)"]);
});

test("a finding a later round defers shows its follow-up, and one still open shows none", async () => {
  const shown = await card([
    { round: 1, outcome: "unresolved", entries: [entry(AT, A, 1, "changes-requested", [
      finding("at-1", "P2", "Deferred later", { state: "open", deferredIssue: undefined }),
      finding("at-2", "P2", "Still open", { state: "open", deferredIssue: "Not a deferral." })])] },
    { round: 2, outcome: "unresolved", entries: [entry(AT, B, 2, "changes-requested", [],
      [{ id: "at-1", state: "deferred", resolution: "Later.", deferredIssue: "WORK-9" }])] }],
    B, [{ click: ".run-pick", index: 0 }]);
  expect(rows(shown)).toEqual([["Deferred later.", "deferred", "↳ WORK-9"], ["Still open.", "open", null]]);
});

test("a finding's row is its text across the card, clamped at three lines, with no column beside it", () => {
  const dom = new JSDOM(pageWithRuns(record([], A), {}, { config: CONFIG }));
  const rules = [];
  for (const sheet of dom.window.document.styleSheets) {
    for (const rule of sheet.cssRules) { if (rule.selectorText) { rules.push(rule); } }
  }
  dom.window.close();
  const style = (selector) => rules.filter((rule) => rule.selectorText === selector).map((rule) => rule.style);
  expect(style("li.run-finding").some((s) => s.getPropertyValue("display") === "block")).toBe(true);
  expect(style("li.run-finding .run-finding-line").some((s) => s.getPropertyValue("-webkit-line-clamp") === "3")).toBe(true);
  expect(rules.filter((rule) => /run-marks|run-filed/.test(rule.selectorText))).toEqual([]);
});
