// The plan chapter's review: one line beside the approval, the "Plan review" fold drawn by the run summary's track and
// cards, and the flag for approvals that block work. Every test fixes the clock and the time zone.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, block, expect, openPage, pageWithRuns } from "./support.mjs";
import { planReviewDelivery, recordOnlyDelivery, threeRoundReview } from "./plan-fixtures.mjs";

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
const flagWhy = (app) => [...app.querySelectorAll("#plan .plan-needs .a2")].map((node) => node.textContent);
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
    "Reviewed candidate 3333333Round 3ApprovedTestingCorrectnessNothing new raisedConfirmed 1 earlier note fixed in 3333333" +
    "CorrectnessThe off-by-one is corrected: scenario 6 reads rounds 2 to 10.fixedc2-1 · The off-by-one is corrected: scenario 6 reads rounds 2 to 10.");
  const [, , first] = await openPage(render(document, plan), { now: NOW, store: false,
    steps: [{ toggle: FOLD }, { click: `${FOLD} .run-pick`, index: 0 }] });
  expect(first.app.querySelector(`${FOLD} .run-card`).textContent).toBe(
    "Reviewed candidate 1111111Round 1Changes requestedTestingSeriousA review past the caps blocks the plan write.CorrectnessSerious · 3" +
    "The fold has no valid shape.The guarantees conflict.The caps block every plan write.All 4 fixed in 2222222 · confirmed round 2");
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
    "Reviewed candidate aaaaaaaRounds 2–10Changes requestedTestingapprovedCorrectness11 notes9 serious2 moderate" +
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
  expect(flags(app)).toEqual(["Approvals are blocking work"]);
  expect(flagWhy(app)).toEqual(["Product Owner signs off the copy; Security reviews the token scope"]);
  expect(app.querySelector("#plan .plan-calm")).toBeNull();
  const broken = structuredClone(document);
  broken.journal.push({ id: 99, at: "2026-10-30T11:00:00Z", kind: "base.broken", subject: null, summary: "main is red" });
  expect(flags(await shown(broken, plan))).toEqual(["The base is broken", "Approvals are blocking work"]);
  // One done: the other still blocks. Both done: the calm line returns, and the fold counts nothing ahead.
  plan.approvals[0].state = "done";
  expect(flagWhy(await shown(document, plan))).toEqual(["Security reviews the token scope"]);
  plan.approvals[1].state = "done";
  app = await shown(document, plan);
  expect(flags(app)).toEqual([]);
  expect(app.querySelector("#plan .plan-calm").textContent).toBe("Nothing in the plan needs you right now.");
  expect(folds(app)[4]).toBe("Approvals ahead1");
});

test("an approval that is waiting but not blocking raises nothing, and no review adds no element", async () => {
  const { document, plan } = recordOnlyDelivery();
  const before = await shown(document, plan);
  plan.approvals = [{ id: "approval-1", text: "Release sign-off", state: "waiting" },
    { id: "approval-2", text: "Copy review", state: "waiting", blocking: false }];
  const after = await shown(document, plan);
  expect(flags(after)).toEqual([]);
  expect(after.querySelector("#plan .plan-calm").textContent).toBe("Nothing in the plan needs you right now.");
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
