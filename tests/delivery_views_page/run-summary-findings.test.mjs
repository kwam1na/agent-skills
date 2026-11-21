// A finished round's findings fit their card. A band heading says the fate its findings share once ("Minor · 2 · all
// open"), and a finding says its own fate only when it differs. A deferral says more: a quiet line under its text gives
// why it waited, from the rule that let it, and how it ended, from its own lens's later rounds; its text is a button
// that opens what could break, the change it proposes, its follow-up and its history, round by round. There is no
// marks column: a row is its text across the card, clamped at three lines. A round whose findings are all fixed or all
// open says so once, in its footer.
process.env.TZ = "UTC";

import { test } from "node:test";
import { JSDOM } from "jsdom";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";

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
async function card(outcomes, head, steps = [], change = () => {}) {
  return (await cards(outcomes, head, steps, change)).at(-1);
}
/** The card after each step, the story's opening included; `change` edits the record first. */
async function cards(outcomes, head, steps = [], change = () => {}) {
  const document = record(outcomes, head);
  change(document);
  const snaps = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { steps: [{ click: '[data-subject="WORK-1"]' }, ...steps] });
  return snaps.slice(1).map((snap) => snap.app.querySelector("#detail .run-card"));
}

// Each deferral as [text, reason, fate], and its fate's class.
const deferrals = (card) => [...card.querySelectorAll("li.run-finding.is-deferral")].map((row) => [
  row.querySelector(".run-finding-text").textContent, row.querySelector(".run-reason").textContent,
  row.querySelector(".run-deferral-said .run-fate").textContent]);
const fateClasses = (card) => [...card.querySelectorAll("li.run-finding.is-deferral .run-fate")].map((fate) => fate.className);
// A deferral's detail, label by label.
const detailOf = (row) => Object.fromEntries([...row.querySelectorAll(".run-deferral-detail > dt")].map((label) =>
  [label.textContent, label.nextElementSibling.textContent]));
const counts = (card) => [...card.querySelectorAll(".run-deferral-count")].map((count) => count.textContent);
const LAST = "no later round to check it · becomes a follow-up at close-out";

test("a band of deferrals names its severity and count, and each deferral says its own reason and fate", async () => {
  const shown = await card([{ round: 1, outcome: "aligned", entries: [
    entry(AT, A, 1, "aligned", [finding("at-1", "P2", "One."), finding("at-2", "P2", "Two."), finding("at-3", "P2", "Three."),
                                finding("at-4", "P2", "Four."), finding("at-5", "P3", "Five."), finding("at-6", "P3", "Six.")]),
    entry(OC, A, 1, "aligned", [finding("oc-1", "P3", "Seven.")])] }], A);
  expect(bands(shown)).toEqual(["Moderate · 4", "Minor · 2", "Minor"]);
  const moderate = "Moderate, so it doesn't block", minor = "Minor, so it doesn't block";
  expect(deferrals(shown)).toEqual([["One.", moderate, LAST], ["Two.", moderate, LAST], ["Three.", moderate, LAST], ["Four.", moderate, LAST],
                                    ["Five.", minor, LAST], ["Six.", minor, LAST], ["Seven.", minor, LAST]]);
  expect(shown.querySelectorAll(".run-marks, .run-filed, .run-followup").length).toBe(0);
  expect(counts(shown)).toEqual(["deferred 6: 6 with no later round", "deferred 1: 1 with no later round"]);
  // The round's chip is as it was.
  expect(shown.querySelector(".run-card-outcome").textContent).toBe("Approved with 7 deferrable notes");
});

test("a finding whose fate differs from its band's says its own, after its text, and a deferral left out still waits", async () => {
  const shown = await card([
    { round: 1, outcome: "unresolved", entries: [entry(AT, A, 1, "changes-requested", [
      finding("at-1", "P2", "Fixed later", { state: "open", deferredIssue: undefined }),
      finding("at-2", "P2", "Still open", { state: "open", deferredIssue: undefined }),
      finding("at-3", "P2", "Left deferred")])] },
    { round: 2, outcome: "unresolved", entries: [entry(AT, B, 2, "aligned", [],
      [{ id: "at-1", state: "closed", fixCommit: B, resolution: "Fixed." }])] }],
    B, [{ click: ".run-pick", index: 0 }]);
  // A band holding a deferral says no shared fate; the others say their own, in the fate colours.
  expect(bands(shown)).toEqual(["Moderate · 3"]);
  expect(rows(shown).slice(0, 2)).toEqual([["Fixed later.", "✓ fixed", null], ["Still open.", "open", null]]);
  expect(deferrals(shown)).toEqual([["Left deferred.", "Moderate, so it doesn't block", "still waiting after round 2"]]);
  expect([...shown.querySelectorAll("li.run-finding .run-fate")].map((fate) => fate.className))
    .toEqual(["run-fate is-fixed", "run-fate is-open", "run-fate is-waiting"]);
  expect(detailOf(shown.querySelector("li.is-deferral"))["How it ended"])
    .toBe("R1Raised and deferred by Adversarial testing.R2Left out of Adversarial testing's report, so it is still deferred.");
});

test("a round whose findings are all fixed says so once below them, and each deferral says which round closed it", async () => {
  const shown = await card([
    { round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [finding("at-1", "P3", "One."), finding("at-2", "P2", "Two.")])] },
    { round: 2, outcome: "aligned", entries: [entry(AT, B, 2, "aligned", [],
      [{ id: "at-1", state: "closed", fixCommit: B, resolution: "Fixed." }, { id: "at-2", state: "fixed", fixCommit: B, resolution: "Fixed." }])] }],
    B, [{ click: ".run-pick", index: 0 }]);
  expect(bands(shown)).toEqual(["Moderate", "Minor"]);
  expect(deferrals(shown).map(([said, , fate]) => [said, fate])).toEqual([["Two.", "✓ fixed in round 2"], ["One.", "✓ closed in round 2"]]);
  expect(fateClasses(shown)).toEqual(["run-fate is-fixed", "run-fate is-closed"]);
  expect(counts(shown)).toEqual(["deferred 2: 1 fixed by round 2, 1 closed by round 2"]);
  expect(shown.querySelector(".run-settled").textContent).toBe("All 2 of this round's notes were fixed in bbbbbbb, and round 2 confirmed them.");
});

test("a deferral's follow-up that is a ticket the page knows, or an address, is linked; anything else stays text", async () => {
  const shown = await card([{ round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [
    finding("at-1", "P2", "Ticket.", { deferredIssue: "WORK-9" }), finding("at-2", "P2", "Address.", { deferredIssue: "https://example.test/issues/9" }),
    finding("at-3", "P2", "Unknown key.", { deferredIssue: "OTHER-4" }), finding("at-4", "P2", "Script.", { deferredIssue: "javascript:alert(1)" })])] }], A);
  const followUps = [...shown.querySelectorAll("li.is-deferral .run-deferral-detail > dt")].filter((label) => label.textContent === "Its follow-up")
    .map((label) => label.nextElementSibling);
  expect(followUps.map((said) => said.querySelector("a")?.getAttribute("href") ?? null))
    .toEqual(["https://tracker.example/issue/WORK-9", "https://example.test/issues/9", null, null]);
  expect([...shown.querySelectorAll(".run-deferral-detail a")].every((link) => link.getAttribute("rel") === "noopener noreferrer")).toBe(true);
  expect(followUps.map((said) => said.textContent)).toEqual(["WORK-9", "https://example.test/issues/9", "OTHER-4", "javascript:alert(1)"]);
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

// Ten deferrals in round 1; round 2 of the same lens lists seven fixed and three still deferred.
function tenDeferrals() {
  const filed = Array.from({ length: 10 }, (_, i) => finding(`at-${i + 1}`, i < 7 ? "P2" : "P3", `Finding ${i + 1}.`));
  const closures = filed.map((note, i) => i < 7 ? { id: note.id, state: "fixed", fixCommit: B, resolution: `Fixed ${i + 1}.` }
    : { id: note.id, state: "deferred", resolution: "Still deferred.", deferredIssue: "WORK-9" });
  return [{ round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", filed)] },
          { round: 2, outcome: "aligned", entries: [entry(AT, B, 2, "aligned", [], closures)] }];
}

test("each of a round's ten deferrals says its reason and its fate, and the lens counts them", async () => {
  const shown = await card(tenDeferrals(), B, [{ click: ".run-pick", index: 0 }]);
  const moderate = "Moderate, so it doesn't block", minor = "Minor, so it doesn't block";
  expect(deferrals(shown)).toEqual([
    ...Array.from({ length: 7 }, (_, i) => [`Finding ${i + 1}.`, moderate, "✓ fixed in round 2"]),
    ...Array.from({ length: 3 }, (_, i) => [`Finding ${i + 8}.`, minor, "still waiting after round 2"])]);
  expect(counts(shown)).toEqual(["deferred 10: 7 fixed by round 2, 3 still waiting"]);
  // The bands agree with the rows they head.
  expect(bands(shown)).toEqual(["Moderate · 7", "Minor · 3"]);
  expect(shown.querySelectorAll("li.run-finding").length).toBe(10);
});

test("a deferral another lens lists fixed, and one a later round leaves out, both still wait", async () => {
  // A native record can't hold another lens's closure, so this is a run's round reports, whose closures name their lens
  // only when the report does.
  const raised = [{ id: "at-1", lens: AT, severity: "P2", summary: "Fixed by another.", state: "resolved", filed: "deferred", deferredIssue: null },
                  { id: "at-2", lens: AT, severity: "P3", summary: "Left out.", state: "deferred", filed: "deferred", deferredIssue: null }];
  const runs = { "run-1": { runId: "run-1", ticket: null, open: false, status: "complete", startedAt: null, lastAt: null, durationSeconds: null,
    rounds: 2, findings: null, roundDetail: [
      { round: 1, at: "2026-10-22T04:40:00Z", outcome: "approve", findings: { P0: 0, P1: 0, P2: 1, P3: 1 },
        verdicts: { [AT]: "approve", [OC]: "approve" }, raised, settled: [] },
      { round: 2, at: "2026-10-22T04:58:00Z", outcome: "approve", findings: { P0: 0, P1: 0, P2: 0, P3: 0 },
        verdicts: { [AT]: "approve", [OC]: "approve" }, raised: [],
        settled: [{ id: "at-1", lens: OC, state: "fixed", fixCommit: B, deferredIssue: null, resolution: "Not mine to fix." }] }] } };
  const document = { state: { lanes: [], queue: [{ subject: "WORK-1", title: "Work", stage: "merged", head: B, runId: "run-1" }] }, journal: [] };
  const snaps = await openPage(pageWithRuns(document, runs, { config: { ...CONFIG, runs: REPLAYED_RUNS } }),
    { steps: [{ click: '[data-subject="WORK-1"]' }, { click: "#detail .run-pick", index: 0 }] });
  const shown = snaps.at(-1).app.querySelector("#detail .run-card");
  expect(deferrals(shown).map((row) => row[2])).toEqual(["still waiting after round 2", "still waiting after round 2"]);
  expect(counts(shown)).toEqual(["deferred 2: 2 still waiting"]);
  // The footer agrees: neither counts as fixed.
  expect(shown.querySelector(".run-settled")).toBeNull();
});

test("a deferral the story's last round raised has no later round, and one the close-out filed reads Filed as its key", async () => {
  const outcomes = [{ round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [finding("at-1", "P2", "Filed."), finding("at-2", "P3", "Not yet.")])] }];
  const shown = await card(outcomes, A, [], (document) => {
    document.state.followups = [{ id: "WORK-1-at-1", title: "Filed", reference: "WORK-9" }, { id: "WORK-1-at-2", title: "Not yet", reference: null }];
  });
  expect(deferrals(shown).map((row) => row[2])).toEqual(["Filed as WORK-9", LAST]);
  expect(fateClasses(shown)).toEqual(["run-fate is-filed", "run-fate is-last"]);
  expect(shown.querySelector(".run-fate.is-filed a").getAttribute("href")).toBe("https://tracker.example/issue/WORK-9");
  expect(counts(shown)).toEqual(["deferred 2: 1 filed, 1 with no later round"]);
  const [filed, waiting] = shown.querySelectorAll("li.is-deferral");
  expect(detailOf(filed)["How it ended"]).toBe("R1Raised and deferred by Adversarial testing.—Filed as WORK-9 at close-out.");
  expect(detailOf(waiting)["How it ended"]).toBe("R1Raised and deferred by Adversarial testing.—No later round to check it; it becomes a follow-up at close-out.");
});

test("a serious finding a verification round deferred outside the delta says it doesn't block this story", async () => {
  const shown = await card([
    { round: 1, outcome: "unresolved", entries: [entry(AT, A, 1, "changes-requested", [finding("at-1", "P1", "First.", { state: "open", deferredIssue: undefined })])] },
    { round: 2, outcome: "aligned", entries: [entry(AT, B, 2, "aligned", [finding("at-2", "P1", "Outside the delta.")],
      [{ id: "at-1", state: "fixed", fixCommit: B, resolution: "Fixed." }])] }], B, [], (document) => {
    document.reviewEvidence["WORK-1"].outcomes[1].entries[0].result.lateFindings = ["at-2"];
  });
  expect(deferrals(shown)).toEqual([["Outside the delta.", "Outside this round's change, so it doesn't block this story", LAST]]);
  expect(detailOf(shown.querySelector("li.is-deferral"))["Why it waited"])
    .toBe("Serious (P1), filed new by a verification round against lines outside the round's change, so Adversarial testing deferred it to a follow-up.");
});

test("a deferral opens and closes on a click or a key, flipping aria-expanded and showing its five fields", async () => {
  const detailed = finding("at-1", "P2", "Detailed.", { failureScenario: "A failed run reports success.",
    proposedChange: "Assert the exit status.", deferredIssue: "Pin that claim exits with its argv's own non-zero status, whatever it is." });
  const outcomes = [{ round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [detailed])] },
                    { round: 2, outcome: "aligned", entries: [entry(AT, B, 2, "aligned", [], [{ id: "at-1", state: "fixed", fixCommit: B, resolution: "A new subtest asserts exit 3." }])] }];
  const keyed = (window) => {
    const button = window.document.querySelector("#detail li.is-deferral .run-deferral");
    button.focus();
    // A real button: the browser turns Enter into its click, which a keydown first marks as the keyboard's.
    button.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    button.click();
  };
  const [, resting, clicked, closed, keyedOpen] = await cards(outcomes, B, [{ click: ".run-pick", index: 0 },
    { click: "#detail li.is-deferral .run-deferral" }, { click: "#detail li.is-deferral .run-deferral" }, { run: keyed }]);
  const state = (shown) => {
    const row = shown.querySelector("li.is-deferral");
    return [row.querySelector(".run-deferral").tagName, row.querySelector(".run-deferral").getAttribute("type"),
            row.querySelector(".run-deferral").getAttribute("aria-expanded"), row.querySelector(".run-deferral-detail").hidden];
  };
  expect(state(resting)).toEqual(["BUTTON", "button", "false", true]);
  expect(state(clicked)).toEqual(["BUTTON", "button", "true", false]);
  expect(state(closed)).toEqual(["BUTTON", "button", "false", true]);
  expect(state(keyedOpen)).toEqual(["BUTTON", "button", "true", false]);
  expect(detailOf(clicked.querySelector("li.is-deferral"))).toEqual({
    "Why it waited": "Moderate (P2). A finding at this level doesn't block landing, so Adversarial testing deferred it to a follow-up.",
    "What could break": "A failed run reports success.",
    "Proposed change": "Assert the exit status.",
    "Its follow-up": "Pin that claim exits with its argv's own non-zero status, whatever it is.",
    "How it ended": "R1Raised and deferred by Adversarial testing.R2Fixed: A new subtest asserts exit 3."});
  // Closed, the row is its text and its one quiet line, as it was at rest.
  expect(closed.querySelector("li.is-deferral").textContent).toBe(resting.querySelector("li.is-deferral").textContent);
});

test("a withdrawn deferral and a closed one say so in their own words, and the withdrawn one shows the lens's reason", async () => {
  const shown = await card([
    { round: 1, outcome: "aligned", entries: [entry(AT, A, 1, "aligned", [finding("at-1", "P2", "Withdrawn."), finding("at-2", "P2", "Closed.")])] },
    { round: 2, outcome: "aligned", entries: [entry(AT, B, 2, "aligned", [], [{ id: "at-1", state: "deferred", resolution: "Still deferred.", deferredIssue: "WORK-9" }])] },
    { round: 3, outcome: "aligned", entries: [entry(AT, B, 3, "aligned", [],
      [{ id: "at-1", state: "withdrawn", resolution: "Not a problem: the guard runs first." }, { id: "at-2", state: "closed", fixCommit: B, resolution: "Closed on the new evidence." }])] }],
    B, [{ click: ".run-pick", index: 0 }]);
  expect(deferrals(shown).map((row) => row[2])).toEqual(["withdrawn in round 3", "✓ closed in round 3"]);
  expect(fateClasses(shown)).toEqual(["run-fate is-withdrawn", "run-fate is-closed"]);
  const [withdrawn] = shown.querySelectorAll("li.is-deferral");
  expect(detailOf(withdrawn)["How it ended"])
    .toBe("R1Raised and deferred by Adversarial testing.R2Still deferred: Still deferred.R3Withdrawn: Not a problem: the guard runs first.");
  expect(counts(shown)).toEqual(["deferred 2: 1 closed by round 3, 1 withdrawn"]);
});

test("a round without deferrals draws no reason, fate line, button or count", async () => {
  const shown = await card([{ round: 1, outcome: "unresolved", entries: [entry(AT, A, 1, "changes-requested", [
    finding("at-1", "P1", "Open.", { state: "open", deferredIssue: undefined })])] }], A);
  expect(shown.querySelectorAll(".run-deferral, .run-deferral-said, .run-deferral-detail, .run-deferral-count, .run-reason").length).toBe(0);
  expect(rows(shown)).toEqual([["Open.", null, null]]);
});

test("a run's round report, whose closures name no lens, ends its deferrals in its own words", async () => {
  const raised = [{ id: "at-1", lens: AT, severity: "P2", summary: "Closed.", state: "resolved", filed: "deferred", deferredIssue: null },
                  { id: "at-2", lens: AT, severity: "P3", summary: "Withdrawn.", state: "resolved", filed: "deferred", deferredIssue: null }];
  const runs = { "run-1": { runId: "run-1", ticket: null, open: false, status: "complete", startedAt: null, lastAt: null, durationSeconds: null,
    rounds: 2, findings: null, roundDetail: [
      { round: 1, at: "2026-10-22T04:40:00Z", outcome: "approve", findings: { P0: 0, P1: 0, P2: 1, P3: 1 }, verdicts: { [AT]: "approve" }, raised, settled: [] },
      { round: 2, at: "2026-10-22T04:58:00Z", outcome: "approve", findings: { P0: 0, P1: 0, P2: 0, P3: 0 }, verdicts: { [AT]: "approve" }, raised: [],
        settled: [{ id: "at-1", state: "closed", fixCommit: B, deferredIssue: null }, { id: "at-2", state: "withdrawn", fixCommit: null, deferredIssue: null }] }] } };
  const document = { state: { lanes: [], queue: [{ subject: "WORK-1", title: "Work", stage: "merged", head: B, runId: "run-1" }] }, journal: [] };
  const snaps = await openPage(pageWithRuns(document, runs, { config: { ...CONFIG, runs: REPLAYED_RUNS } }),
    { steps: [{ click: '[data-subject="WORK-1"]' }, { click: "#detail .run-pick", index: 0 }] });
  const shown = snaps.at(-1).app.querySelector("#detail .run-card");
  expect(deferrals(shown).map((row) => row[2])).toEqual(["✓ closed in round 2", "withdrawn in round 2"]);
  expect(detailOf(shown.querySelectorAll("li.is-deferral")[1])["How it ended"]).toBe("R1Raised and deferred by Adversarial testing.R2Withdrawn.");
});
