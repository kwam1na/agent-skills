// The plan chapter's review: one line beside the approval, the "Plan review" fold drawn by the run summary's track and
// cards, and the flag for approvals that block work. Every test fixes the clock and the time zone.
process.env.TZ = "UTC";

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import vm from "node:vm";
import { JSDOM } from "jsdom";

import { REPLAYED_RUNS, ROOT, block, expect, openPage, pageWithRuns } from "./support.mjs";
import { LENSES, PLANNING_CASES, PREVIEW_CASES, planReviewDelivery, previewDelivery, recordOnlyDelivery, threeRoundReview } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  lenses: { "lens.outcome-correctness": { name: "Correctness" }, "lens.adversarial-testing": { name: "Testing" } } };

function render(document, plan) {
  return pageWithRuns(document, {}, { config: CONFIG, plan });
}
async function shown(document, plan, steps = []) {
  const snaps = await openPage(render(document, plan), { now: NOW, store: false, steps });
  return snaps[snaps.length - 1].app;
}
const FOLD = '.plan-fold[data-fold="review"]';
const flags = (app) => [...app.querySelectorAll("#plan .plan-needs .a1")].map((node) => node.textContent);
// Blocking approvals are listed one per row under their flag, by title.
const approvalRows = (app) => [...app.querySelectorAll("#plan .plan-needs .ap-list .ap-title")].map((node) => node.textContent);
const line = (app) => app.querySelector("#plan .plan-approval")?.textContent ?? null;
const folds = (app) => [...app.querySelectorAll("#plan .plan-folds > .plan-fold > summary")].map((node) => node.textContent);

test("the approval line reads the review's rounds and how it ended, and is absent without a review", async () => {
  const { document, plan } = planReviewDelivery();
  expect(line(await shown(document, plan))).toBe("Approved Oct 30 after 3 review rounds · every lens aligned");
  const ended = structuredClone(plan);
  ended.planReview.state = "ended";
  ended.planReview.endedReason = "The findings stopped converging; the operator ended the review.";
  ended.planReview.rounds[2].verdicts["lens.outcome-correctness"] = "changes";
  expect(line(await shown(document, ended))).toBe("Approved Oct 30 after 3 review rounds · ended without alignment");
  const open = structuredClone(plan);
  open.planReview.state = "open";
  open.planReview.rounds.pop();
  expect(line(await shown(document, open))).toBe("Approved Oct 30 after 2 review rounds · still open");
  const single = structuredClone(plan);
  single.planReview.rounds = [{ ...single.planReview.rounds[2], round: 1, settled: [] }];
  expect(line(await shown(document, single))).toBe("Approved Oct 30 after 1 review round · every lens aligned");
  // Without a review the chapter is today's: the approval's own text stays in the units fold.
  const plain = structuredClone(plan);
  delete plain.planReview;
  const app = await shown(document, plain);
  expect(line(app)).toBeNull();
  expect(app.querySelector(FOLD)).toBeNull();
  expect(app.querySelector("#plan .ps-when").textContent).toBe("Approved 6 hours ago · after 3 review rounds · every lens aligned");
});

test("the line sits beside the approval, open, and the review is one closed fold named by its rounds", async () => {
  const { document, plan } = planReviewDelivery();
  const app = await shown(document, plan);
  expect([...app.querySelector("#plan .inner").children].map((node) => node.className)).toEqual(
    ["kicker", "title plan-title", "say plan-outcome", "plan-approval", "plan-needs", "plan-open", "plan-folds"]);
  expect(folds(app)).toEqual(["All units1", "Plan review (3 rounds)", "Changes since approval0", "Waived proofs0",
    "Approvals ahead3", "Out of scope2", "The full outcome"]);
  expect(app.querySelector(FOLD).open).toBe(false);
  // N is the last entry's round, the rounds the review ran, not the entries the file holds.
  const folded = structuredClone(plan);
  folded.planReview = threeRoundReview({ folded: true });
  const many = await shown(document, folded);
  expect(line(many)).toBe("Approved Oct 30 after 12 review rounds · every lens aligned");
  expect(folds(many)[1]).toBe("Plan review (12 rounds)");
});

test("the fold draws the track and each round's card, with the closing commit of each fixed finding", async () => {
  const { document, plan } = planReviewDelivery();
  const app = await shown(document, plan, [{ toggle: FOLD }]);
  const fold = app.querySelector(FOLD);
  expect(fold.querySelector(".run-quiet").textContent).toBe("The plan's review has no round bound: it runs until every lens approves or the operator ends it.");
  expect(fold.querySelector(".run-story").textContent).toBe("Approved on round 3 · 5 serious notes raised");
  expect([...fold.querySelectorAll(".run-pick")].map((pick) => pick.getAttribute("aria-label"))).toEqual(
    ["Round 1, asked for changes, 4 serious notes", "Round 2, asked for changes, 1 serious note", "Round 3, approved, nothing raised"]);
  // The newest round is picked: it raised nothing and confirmed round 2's finding fixed in round 3's commit.
  expect(fold.querySelector(".run-card").textContent).toBe(
    "Reviewed at 3333333Round 3ApprovedTestingCorrectnessNothing new raisedThis round confirmed 1 earlier note: 1 fixed in 3333333." +
    "Correctness · 1 fixedScenario 6 reads the wrong rounds.seriousfixedFixed in 3333333The off-by-one is corrected: scenario 6 reads rounds 2 to 10.");
  // The fix sits on its own sheet under the finding, headed by its commit; the note's id line is gone once its finding is known.
  expect(fold.querySelector(".run-card .run-closure-fix").textContent).toBe("Fixed in 3333333The off-by-one is corrected: scenario 6 reads rounds 2 to 10.");
  expect(fold.querySelector(".run-card .run-closure")).toBeNull();
  const [, , first] = await openPage(render(document, plan), { now: NOW, store: false,
    steps: [{ toggle: FOLD }, { click: `${FOLD} .run-pick`, index: 0 }] });
  expect(first.app.querySelector(`${FOLD} .run-card`).textContent).toBe(
    "Reviewed at 1111111Round 1Changes requestedTestingSeriousA review past the caps blocks the plan write.CorrectnessSerious · 3" +
    "The fold has no valid shape.The guarantees conflict.The caps block every plan write.All 4 of this round's notes were fixed in 2222222, and round 2 confirmed them.");
});

test("a fold entry reads as its rounds with their counts, and an entry says what it doesn't list", async () => {
  const { document, plan } = planReviewDelivery();
  plan.planReview = threeRoundReview({ folded: true });
  const snaps = await openPage(render(document, plan), { now: NOW, store: false,
    steps: [{ toggle: FOLD }, { click: `${FOLD} .run-pick`, index: 1 }, { click: `${FOLD} .run-pick`, index: 2 }] });
  const fold = snaps[1].app.querySelector(FOLD);
  expect([...fold.querySelectorAll(".run-dot")].map((dot) => dot.textContent)).toEqual(["1", "2–10", "11", "12"]);
  expect(fold.querySelectorAll(".run-pick")[1].getAttribute("aria-label")).toBe("Rounds 2–10, asked for changes, 9 serious notes, 2 moderate notes");
  expect(snaps[2].app.querySelector(`${FOLD} .run-card`).textContent).toBe(
    "Reviewed at aaaaaaaRounds 2–10Changes requestedTestingapprovedCorrectness11 notes9 serious2 moderate" +
    "This entry folds 9 rounds and doesn't list 11 findings or 7 closures.");
  expect(snaps[3].app.querySelector(`${FOLD} .run-clipped`).textContent).toBe("This round doesn't list 5 findings, and some of its texts were shortened.");
});

test("an ended review shows its reason in the fold, never in the line", async () => {
  const { document, plan } = planReviewDelivery();
  plan.planReview.state = "ended";
  plan.planReview.endedReason = "The findings stopped converging; the operator ended the review.";
  plan.planReview.rounds[2].verdicts["lens.outcome-correctness"] = "changes";
  const app = await shown(document, plan, [{ toggle: FOLD }]);
  expect(app.querySelector(`${FOLD} .run-ended`).textContent).toBe("Ended without alignment: The findings stopped converging; the operator ended the review.");
  expect(line(app)).not.toContain("converging");
});

test("blocking approvals raise one flag naming each, after a broken base, until each is done", async () => {
  const { document, plan } = planReviewDelivery();
  let app = await shown(document, plan);
  expect(flags(app)).toEqual(["2 approvals are blocking work"]);
  expect(approvalRows(app)).toEqual(["Product Owner signs off the copy", "Security reviews the token scope"]);
  // Each row is its title alone: a numbering id (approval-1) and an absent owner say nothing, and a row with no more to say doesn't open.
  expect([...app.querySelectorAll("#plan .ap-row")].map((row) => [row.querySelector(".ap-id").textContent, row.querySelector(".ap-who").textContent,
    row.classList.contains("is-whole")])).toEqual([["", "", true], ["", "", true]]);
  expect(app.querySelector("#plan .plan-needs .ap-how").textContent).toBe("Tell the agent leading this which you approve; it records each one in the plan.");
  expect(app.querySelector("#plan .plan-calm")).toBeNull();
  const broken = structuredClone(document);
  broken.journal.push({ id: broken.journal.length + 1, at: "2026-10-30T11:00:00Z", kind: "base.broken", lane: null, subject: null, data: { source: "queue", detail: "Main is red" } });
  expect(flags(await shown(broken, plan))).toEqual(["The base is broken", "2 approvals are blocking work"]);
  // One done: the other still blocks. Both done: the calm line returns, and the fold counts nothing ahead.
  plan.approvals[0].state = "done";
  const one = await shown(document, plan);
  expect([flags(one), approvalRows(one)]).toEqual([["An approval is blocking work"], ["Security reviews the token scope"]]);
  plan.approvals[1].state = "done";
  app = await shown(document, plan);
  expect(flags(app)).toEqual([]);
  expect(app.querySelector("#plan .plan-calm").textContent).toBe("Nothing in the plan needs attention right now.");
  expect(folds(app)[4]).toBe("Approvals ahead1");
});

test("an approval that is waiting but not blocking raises nothing, and no review adds no element", async () => {
  const { document, plan } = recordOnlyDelivery();
  const before = await shown(document, plan);
  plan.approvals = [{ id: "approval-1", text: "Release sign-off", state: "waiting" },
    { id: "approval-2", text: "Copy review", state: "waiting", blocking: false }];
  const after = await shown(document, plan);
  expect(flags(after)).toEqual([]);
  expect(after.querySelector("#plan .plan-calm").textContent).toBe("Nothing in the plan needs attention right now.");
  expect(after.querySelector("#plan .plan-approval")).toBeNull();
  expect(after.querySelector(FOLD)).toBeNull();
  expect([...after.querySelector("#plan .inner").children].map((node) => node.className)).toEqual(
    [...before.querySelector("#plan .inner").children].map((node) => node.className));
});

test("review text never becomes markup", async () => {
  const { document, plan } = planReviewDelivery();
  plan.planReview.rounds[0].raised[0].summary = "<img src=x onerror=alert(1)>";
  plan.planReview.rounds[1].settled[0].resolution = "<b>bold</b>";
  const page = render(document, plan);
  expect(block(page, "delivery-plan").plan.planReview.rounds[0].raised[0].summary).toBe("<img src=x onerror=alert(1)>");
  const snaps = await openPage(page, { now: NOW, store: false, steps: [{ toggle: FOLD }, { click: `${FOLD} .run-pick`, index: 0 }] });
  expect(snaps[2].app.querySelectorAll("img").length).toBe(0);
  expect([...snaps[1].app.querySelectorAll(`${FOLD} b`)].some((node) => node.textContent === "bold")).toBe(false);
  expect(snaps[2].app.querySelector(`${FOLD} .run-card`).textContent).toContain("<img src=x onerror=alert(1)>");
});

// A plan preview: a plan under agent review, which no person has approved. Its chapter never reads as approved.
const PREVIEW_PAGES = new Map();
function previewPage(name) {
  if (!PREVIEW_PAGES.has(name)) {
    const { document, plan } = (PREVIEW_CASES[name] ?? PLANNING_CASES[name])();
    PREVIEW_PAGES.set(name, render(document, plan));
  }
  return PREVIEW_PAGES.get(name);
}
async function previewShown(name, steps = []) {
  const snaps = await openPage(previewPage(name), { now: NOW, store: false, steps });
  return snaps[snaps.length - 1].app;
}
/**
 * The plan chapter's words and labels, leaving out by node, not by wording, the approvals fold's title and the plan's own
 * approval items, which may say "approve" as the plan wrote them. The preview's own line ends "not approved by a person",
 * which says the opposite of approved: that one phrase is left out of that one node, and the line is pinned whole above.
 */
function unapprovedText(app) {
  const chapter = app.querySelector("#plan").cloneNode(true);
  for (const node of chapter.querySelectorAll('.plan-fold[data-fold="approvals"] > summary, .plan-approvals > li')) node.remove();
  const own = chapter.querySelector(".plan-approval");
  if (own && own.textContent.endsWith(" · not approved by a person")) own.textContent = own.textContent.slice(0, -" · not approved by a person".length);
  const labels = [...chapter.querySelectorAll("[aria-label], [title]")].map((node) => `${node.getAttribute("aria-label") ?? ""} ${node.getAttribute("title") ?? ""}`);
  const tab = app.querySelector('.rail a[href="#plan"]');
  return [chapter.textContent, ...labels, tab.textContent, tab.getAttribute("aria-label")].join("\n");
}
const OVER_SHARE = "preview-over-share";
function overSharePage() {
  if (!PREVIEW_PAGES.has(OVER_SHARE)) {
    const { document, plan } = previewDelivery({ rounds: 2 });
    PREVIEW_PAGES.set(OVER_SHARE, pageWithRuns(document, {}, { config: CONFIG, planBlock: { plan, clipped: { changes: 0, texts: 4, overShare: true } } }));
  }
  return PREVIEW_PAGES.get(OVER_SHARE);
}

test("a preview's line says agent review only, how far it went, and that no person approved it", async () => {
  expect(line(await previewShown("preview-none"))).toBe("Agent review only · not reviewed yet · not approved by a person");
  expect(line(await previewShown("preview-open"))).toBe("Agent review only · 3 review rounds · still open · not approved by a person");
  expect(line(await previewShown("preview-aligned"))).toBe("Agent review only · 3 review rounds · every lens aligned · not approved by a person");
  expect(line(await previewShown("preview-aligned-first"))).toBe("Agent review only · 1 review round · every lens aligned · not approved by a person");
  expect(line(await previewShown("preview-ended"))).toBe("Agent review only · 2 review rounds · ended without alignment · not approved by a person");
  const handed = await previewShown("preview-handed");
  expect(line(handed)).toBe("Handed to delivery SHOP-90 · 3 review rounds · every lens aligned");
  expect(handed.querySelector("#plan").textContent).not.toContain("not approved by a person");
});

test("a planning record's line names its endorsements, says a stale one, and reads as endorsed only once all are in", async () => {
  const shown = async (name) => {
    const { document, plan } = PLANNING_CASES[name]();
    return (await openPage(render(document, plan), { now: NOW, store: false })).at(-1).app;
  };
  const none = await shown("planning-none");
  expect(line(none)).toBe("Agent review only · 2 review rounds · every lens aligned · not approved by a person");
  expect(line(await shown("planning-partial"))).toBe(
    "Agent review only · 2 review rounds · every lens aligned · endorsed by Ama · stale endorsement by Ben · not approved by a person");
  const endorsed = await shown("planning-endorsed");
  expect(line(endorsed)).toBe("Endorsed by Ama and Ben · 2 review rounds · every lens aligned");
  // Endorsed is no person's approval of the delivery: the chapter never says a form of "approve".
  assert.doesNotMatch(unapprovedText(endorsed), /approv/i);
});

test("no preview chapter reads as approved, at any round's ending, and none draws the switcher or the changes fold", async () => {
  // A planning record's plan chapter is held to the same rule as any preview's.
  const names = [...Object.keys(PREVIEW_CASES), ...Object.keys(PLANNING_CASES)];
  for (const name of names) {
    const app = await previewShown(name);
    const words = unapprovedText(app);
    assert.doesNotMatch(words, /approv/i, name);
    expect([name, app.querySelector("#plan .plan-switch"), app.querySelector('#plan .plan-fold[data-fold="changes"]'),
      app.querySelector("#plan .plan-scoped"), app.querySelector("#plan .ps-when")]).toEqual([name, null, null, null, null]);
    // Every review fold is drawn whole, its newest round's card included, so each ending's pill and verdicts are read.
    if ((PREVIEW_CASES[name] ?? PLANNING_CASES[name])().plan.planReview) expect([name, Boolean(app.querySelector(`${FOLD} .run-card-outcome`))]).toEqual([name, true]);
  }
  // The approving branches each fixture reaches: the round pill, the story head, and a lens's verdict beside the round's.
  expect((await previewShown("preview-aligned-first")).querySelector(`${FOLD} .run-story`).textContent).toBe("Lenses aligned on the first round · nothing raised");
  expect((await previewShown("preview-notes-open")).querySelector(`${FOLD} .run-story`).textContent).toBe("Round 2 aligned, notes open · 2 serious notes raised");
  expect((await previewShown("preview-notes-open")).querySelector(`${FOLD} .run-card-outcome`).textContent).toBe("Aligned, notes open");
  const open = await previewShown("preview-open");
  expect([...open.querySelectorAll(`${FOLD} .run-lens-verdict`)].map((node) => node.textContent)).toEqual(["aligned"]);
  expect([...open.querySelectorAll(`${FOLD} .run-pick`)].map((pick) => pick.getAttribute("aria-label"))).toEqual(
    ["Round 1, asked for changes, 1 serious note", "Round 2, asked for changes, 1 serious note", "Round 3, asked for changes, 1 serious note"]);
  const aligned = await previewShown("preview-aligned");
  expect(aligned.querySelector(`${FOLD} .run-pick[aria-pressed="true"]`).getAttribute("aria-label")).toBe("Round 3, aligned, nothing raised");
  expect(aligned.querySelector(`${FOLD} .run-quiet`).textContent).toBe("The plan's review has no round bound: it runs until every lens aligns or the operator ends it.");
  // The approvals fold keeps the plan's own words, and its title, which the check above leaves out by node.
  expect(folds(aligned)).toEqual(["All units2", "Plan review (3 rounds)", "Decisions2", "Waived proofs0", "Approvals ahead2", "Out of scope1", "The full outcome"]);
  expect([...aligned.querySelectorAll("#plan .plan-approvals > li")].map((node) => node.textContent)).toEqual(
    ["Waiting Product Owner signs off the copy", "Waiting The operator approves the plan"]);
});

test("the unreviewed and the open preview read in full as pinned", async () => {
  const head = "The plan0 of 3 criteria proven.Plan preview (agent review only): Saved addresses";
  const units = "SHOP-91Saved addresses3 proofs left · No review is recorded for it. It hasn't been handed off yet." +
    "SHOP-92Address book export3 proofs left · No review is recorded for it. It hasn't been handed off yet." +
    "All units2UnitCriteriaStatusSHOP-91Saved addressesCriterion 1 of SHOP-91 SHOP-91.1Criterion 2 of SHOP-91 SHOP-91.23 proofs left" +
    "No review is recorded for it.It hasn't been handed off yet.It still has to merge.SHOP-92Address book exportCriterion 1 of SHOP-92 SHOP-92.1" +
    "3 proofs leftNo review is recorded for it.It hasn't been handed off yet.It still has to merge.";
  const tail = "Decisions2Addresses are kept per account, never per device.No new renderer version.Waived proofs0No proof was waived." +
    "Approvals ahead2Waiting Product Owner signs off the copyWaiting The operator approves the planOut of scope1Importing addresses" +
    "The full outcomeShoppers keep their addresses between visits.";
  const calm = "Nothing in the plan needs attention right now.";
  expect((await previewShown("preview-none")).querySelector("#plan .inner").textContent).toBe(
    head + "Agent review only · not reviewed yet · not approved by a person" + calm + units + tail);
  expect((await previewShown("preview-open")).querySelector("#plan .inner").textContent).toBe(
    head + "Agent review only · 3 review rounds · still open · not approved by a person" + calm + units +
    "Plan review (3 rounds)The plan's review has no round bound: it runs until every lens aligns or the operator ends it." +
    "Round 3 asked for changes · 3 serious notes raised113:00 am211 h later311 h later" +
    "Reviewed at 3333333Round 3Changes requestedTestingalignednothing raisedCorrectnessSerious" +
    "Round 3 found a gap in the plan.This round's note is still open.This round confirmed 1 earlier note: 1 fixed in 3333333." +
    "Correctness · 1 fixedRound 2 found a gap in the plan.seriousfixedFixed in 3333333Fixed in round 3." + tail);
});

test("an aligned preview's fold says the lenses aligned, while a delivery's plan review still says approved", async () => {
  const preview = await previewShown("preview-aligned", [{ toggle: FOLD }]);
  expect(preview.querySelector(`${FOLD} .run-story`).textContent).toBe("Lenses aligned on round 3 · 2 serious notes raised");
  expect(preview.querySelector(`${FOLD} .run-card-outcome`).textContent).toBe("Aligned");
  const { document, plan } = planReviewDelivery();
  const delivery = await shown(document, plan, [{ toggle: FOLD }]);
  expect(delivery.querySelector(`${FOLD} .run-story`).textContent).toBe("Approved on round 3 · 5 serious notes raised");
  expect(delivery.querySelector(`${FOLD} .run-card-outcome`).textContent).toBe("Approved");
});

test("the decisions fold lists every decision, and a plan outside the delivery layout says so and shows no units", async () => {
  const app = await previewShown("preview-other");
  const decisions = app.querySelector('#plan .plan-fold[data-fold="decisions"]');
  expect(decisions.querySelector("summary").textContent).toBe("Decisions2");
  expect([...decisions.querySelectorAll(".plan-decisions li")].map((node) => node.textContent)).toEqual(
    ["Addresses are kept per account, never per device.", "No new renderer version."]);
  expect(app.querySelector("#plan .plan-layout").textContent).toBe("This plan isn't in the delivery layout, so it shows no stories.");
  expect(app.querySelector('#plan .plan-fold[data-fold="units"]')).toBeNull();
  expect(app.querySelector("#plan .plan-open")).toBeNull();
  expect(folds(app)).toEqual(["Plan review (2 rounds)", "Decisions2", "Waived proofs0", "Approvals ahead2", "Out of scope1", "The full outcome"]);
  // A decision reads through the page's one formatter: what it quotes as code reads as code, and nothing becomes markup.
  const quoted = previewDelivery({ rounds: 1, decisions: ["Keep `plan.json` whole.", "<b>never markup</b>"] });
  const rich = (await openPage(render(quoted.document, quoted.plan), { now: NOW, store: false })).pop().app;
  const items = [...rich.querySelectorAll("#plan .plan-decisions li")];
  expect(items.map((node) => [node.textContent, node.querySelector("code")?.textContent ?? null, node.querySelector("b")])).toEqual(
    [["Keep plan.json whole.", "plan.json", null], ["<b>never markup</b>", null, null]]);
  // A preview that records no decisions still draws the fold, saying what will appear there, with no zero.
  const none = (await previewShown("preview-no-decisions")).querySelector('#plan .plan-fold[data-fold="decisions"]');
  expect([none.querySelector("summary").textContent, none.querySelector(".pf-count"), none.querySelector(".plan-decisions"),
    none.querySelector(".waiting").textContent]).toEqual(["Decisions", null, null, "The plan's decisions will show here once it records them."]);
  // A delivery-layout preview draws no such line.
  expect((await previewShown("preview-aligned")).querySelector("#plan .plan-layout")).toBeNull();
});

test("a preview over its page's share says so at the chapter's end, in its own words, and never reads as approved", async () => {
  const snaps = await openPage(overSharePage(), { now: NOW, store: false });
  const app = snaps[snaps.length - 1].app;
  const inner = app.querySelector("#plan .inner");
  expect(inner.lastElementChild.className).toBe("plan-clipped");
  expect(inner.lastElementChild.textContent).toBe("The plan was too large to show whole: 4 other texts left out. The plan alone is over the page's share for it.");
  assert.doesNotMatch(unapprovedText(app), /approv/i);
  // A delivery's note keeps its place in the changes fold and its words.
  const { document, plan } = recordOnlyDelivery();
  const delivery = (await openPage(pageWithRuns(document, {}, { config: CONFIG, planBlock: { plan, clipped: { changes: 0, texts: 4, overShare: true } } }),
    { now: NOW, store: false })).pop().app;
  expect(delivery.querySelector('#plan .plan-fold[data-fold="changes"] .plan-clipped').textContent).toBe(
    "The plan was too large to show whole: 4 other texts left out. The approved plan alone is over the page's share for it.");
});

// Every string literal in a script's source, outside comments and regular expressions.
function literals(source) {
  const found = [];
  let previous = "";
  for (let i = 0; i < source.length;) {
    const c = source[i], next = source[i + 1];
    if (c === "/" && next === "/") { const end = source.indexOf("\n", i); i = end < 0 ? source.length : end; continue; }
    if (c === "/" && next === "*") { i = source.indexOf("*/", i + 2) + 2; continue; }
    if (c === '"' || c === "'" || c === "`") {
      let j = i + 1, value = "";
      while (source[j] !== c) { if (source[j] === "\\") { value += source[j + 1]; j += 2; } else { value += source[j]; j += 1; } }
      found.push(value);
      previous = c;
      i = j + 1;
      continue;
    }
    if (c === "/" && (previous === "" || "(,=:[!&|?{};".includes(previous))) {
      let j = i + 1, inClass = false;
      while (source[j] !== "/" || inClass) { if (source[j] === "\\") { j += 1; } else if (source[j] === "[") { inClass = true; } else if (source[j] === "]") { inClass = false; } j += 1; }
      previous = "/";
      i = j + 1;
      continue;
    }
    if (!/\s/.test(c)) previous = c;
    i += 1;
  }
  return found;
}
/** The approving literals in a source with no preview wording in its own table, but the enum values it never shows. */
function unworded(source) {
  const { words, enums } = vm.runInNewContext(`${source}\n;({ words: RUN_PREVIEW_WORDS, enums: RUN_PREVIEW_ENUMS })`, {});
  return literals(source).filter((value) => /approv/i.test(value) && !Object.prototype.hasOwnProperty.call(words, value) && !enums.includes(value));
}

test("every approving string the run summary draws has a preview wording, and a new one fails until it has", () => {
  const source = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "run-summary.js"), "utf8");
  const found = literals(source);
  // The scan reads strings, not comments or patterns: the comment quoting "approved" and the regular expressions are skipped.
  expect(found.includes("Approved on round ")).toBe(true);
  expect(found.includes("approve")).toBe(true);
  expect(found.some((value) => value.includes("yesterday |"))).toBe(false);
  expect(unworded(source)).toEqual([]);
  const { words } = vm.runInNewContext(`${source}\n;({ words: RUN_PREVIEW_WORDS })`, {});
  for (const [from, to] of Object.entries(words)) {
    expect([from, found.includes(from)]).toEqual([from, true]);
    assert.doesNotMatch(to, /approv/i);
  }
  expect(unworded(`${source}\nvar planted = "Approves once the lead says so";`)).toEqual(["Approves once the lead says so"]);
  expect(unworded(source.replace('"Approved, notes open": "Aligned, notes open",', ""))).toEqual(["Approved, notes open"]);
});

test("the run summary takes the preview's wording only when asked, for a deferrable note's pill as for the rest", () => {
  const source = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "run-summary.js"), "utf8");
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  dom.window.eval(source);
  const [oc, at] = LENSES;
  const round = { round: 1, outcome: "approve", verdicts: { [oc]: "approve", [at]: "approve" }, findings: { P0: 0, P1: 0, P2: 1, P3: 0 },
    raised: [{ id: "n1", lens: oc, severity: "P2", summary: "A note for later.", state: "resolved", filed: "deferred" }], settled: [] };
  const summary = { observational: true, observationKey: "k", open: false, roundDetail: [round] };
  const pill = (options) => dom.window.runSummaryNode(summary, options).querySelector(".run-card-outcome").textContent;
  expect(pill({ preview: true })).toBe("Aligned with 1 deferrable note");
  expect(pill({})).toBe("Approved with 1 deferrable note");
  expect(pill(undefined)).toBe("Approved with 1 deferrable note");
  assert.doesNotMatch(dom.window.runSummaryNode(summary, { preview: true }).textContent, /approv/i);
});
