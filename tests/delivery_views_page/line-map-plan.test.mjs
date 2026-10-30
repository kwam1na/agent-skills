// The plan chapter: is the work still what was agreed, and what proves it? Every test fixes the clock and the time
// zone and starts with empty storage.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, ROOT, block, embedded, expect, openPage, pageWithRuns, readJson } from "./support.mjs";
import { APP_KIND, LENSES, RECORD_KINDS, finishLine, headOf, inAppDelivery, item, journal, minute, planFor, record, recordOnlyDelivery, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  lenses: { "lens.outcome-correctness": { name: "Correctness" }, "lens.adversarial-testing": { name: "Testing" } } };
const CHAPTER_ORDER = ["Now", "The line", "Who", "What's left", "The plan", "What happened", "Parked"];

function render(document, plan, { runs = {}, planBlock } = {}) {
  return pageWithRuns(document, runs, { config: CONFIG, ...(plan !== undefined ? { plan } : {}), ...(planBlock !== undefined ? { planBlock } : {}) });
}
function open(page, scenario = {}) {
  return openPage(page, { now: NOW, store: false, ...scenario });
}
async function first(document, plan, options) {
  const [snapshot] = await open(render(document, plan, options));
  return snapshot.app;
}
const rail = (app) => [...app.querySelectorAll(".rail a")].map((link) => link.getAttribute("aria-label"));
const flags = (app) => [...app.querySelectorAll("#plan .plan-needs .a1")].map((node) => node.textContent);
const flagWhy = (app) => [...app.querySelectorAll("#plan .plan-needs .a2")].map((node) => node.textContent);
const title = (app) => app.querySelector("#plan .plan-title").firstChild.textContent;
const openRows = (app) => [...app.querySelectorAll("#plan .plan-open li")].map((row) => row.textContent);
/** Each owed proof of a unit in the all-units grid, by its kind's label: the pill's state class and its words. */
function proofs(app, subject) {
  const row = [...app.querySelectorAll("#plan .plan-all .plan-unit")]
    .find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === subject);
  return Object.fromEntries([...row.querySelectorAll(".plan-pill:not(.none-owed)")]
    .map((pill) => [pill.querySelector(".pp-kind").textContent, `${pill.className.replace("plan-pill ", "")}: ${pill.querySelector(".pp-state").textContent}`]));
}

/** A delivery of units that are each approved, handed off and merged, with two finish lines the record also has. */
function proven(subjects = ["A", "B"], { kinds = RECORD_KINDS, extraSteps = [], queue = null, changes = [], evidence = {}, finish = null } = {}) {
  const steps = [];
  subjects.forEach((subject, index) => {
    steps.push([10 + index * 3, "approve", subject], [11 + index * 3, "candidate.handed", subject], [12 + index * 3, "candidate.merged", subject]);
  });
  const lines = finish ?? [finishLine("landed", { criteria: [], proofs: ["merged"] }), finishLine("all", { criteria: "all", proofs: ["review"] })];
  const document = record({ queue: queue ?? subjects.map((subject) => item(subject, "merged")), journal: journal([...steps, ...extraSteps]),
    finish: lines.map((line) => ({ id: line.id, text: line.text, met: false })) });
  return { document, plan: planFor(subjects.map((subject) => unit(subject)), { kinds, finish: lines, changes, evidence }) };
}

test("without a plan the page has no plan chapter, rail entry or Now line, and What's left keeps its finish-line list", async () => {
  const { document } = recordOnlyDelivery();
  const page = render(document);
  expect(block(page, "delivery-plan")).toBeNull();
  const [shown, detail] = await open(page, { steps: [{ click: '.stop-btn[data-subject="PAY-4"]' }] });
  expect(rail(shown.app)).toEqual(["Now", "The line", "Who", "What's left", "What happened", "Parked"]);
  expect(shown.app.querySelector("#plan")).toBeNull();
  expect(shown.app.querySelector(".plan-now")).toBeNull();
  expect(shown.app.querySelector(".plan-meters")).toBeNull();
  expect(shown.app.querySelector("#left .after").textContent).toBe("NextEvery piece landsThenEvery criterion is proven");
  expect(detail.app.querySelector("#detail .d-plan")).toBeNull();
});

test("an unreadable plan adds only the chapter and its rail entry, with one flag naming the key path", async () => {
  const { document } = recordOnlyDelivery();
  const [absent] = await open(render(document));
  for (const [planBlock, why] of [[{ unreadable: "units[0].title: is required" }, "units[0].title: is required"], ["not a plan", null]]) {
    const [shown] = await open(render(document, undefined, { planBlock }));
    expect(rail(shown.app)).toEqual(CHAPTER_ORDER);
    expect(flags(shown.app)).toEqual(["The plan couldn't be read"]);
    expect(flagWhy(shown.app)).toEqual(why === null ? [] : [why]);
    expect(shown.app.querySelector("#plan .inner").children).toHaveLength(2);
    // Everything else reads as if there were no plan: no Now line, and What's left keeps its list.
    for (const chapter of ["now", "line", "who", "left", "happened", "parked"]) {
      expect(shown.app.querySelector(`#${chapter}`).textContent).toBe(absent.app.querySelector(`#${chapter}`).textContent);
    }
  }
});

test("opens with only what the engineer acts on, and folds the rest behind counts", async () => {
  const { document, plan } = inAppDelivery();
  const app = await first(document, plan);
  expect(rail(app)).toEqual(CHAPTER_ORDER);
  const inner = app.querySelector("#plan .inner");
  expect([...inner.children].map((node) => node.className)).toEqual(
    ["kicker", "title plan-title", "say plan-outcome", "plan-needs", "plan-open", "plan-meters", "plan-folds"]);
  expect(title(app)).toBe("28 of 29 criteria proven, 1 by waiver.");
  expect(app.querySelector("#plan .plan-outcome").textContent).toBe("Shoppers can pay again after a dropped connection.");
  expect(flags(app)).toEqual(["A finish line no longer covers the plan", "A change touches open work"]);
  expect(openRows(app)).toEqual(["CART-105Cart step 5Next: Merged: not merged yet"]);
  expect([...app.querySelectorAll("#plan .plan-meter")].map((meter) => meter.textContent)).toEqual(
    ["Every cart criterion mergedNot met13 of 14 criteria", "Checked in the appNot met27 of 29 criteria, 1 more by waiver"]);
  // Nothing else shows until a fold opens: six folds, each closed, each with its count.
  const folds = [...inner.querySelectorAll(":scope > .plan-folds > .plan-fold")];
  expect(folds.map((fold) => fold.querySelector("summary").textContent)).toEqual(
    ["All units20", "Changes since approval7", "Waived proofs1", "Approvals ahead1", "Out of scope3", "The full outcome"]);
  expect(folds.every((fold) => fold.tagName === "DETAILS" && !fold.open)).toBe(true);
  expect(app.querySelectorAll("#plan details").length).toBe(6);
  // Now names what is being built and how much is proven, and links to the chapter.
  const now = app.querySelector("#now .plan-now");
  expect(now.textContent).toBe("Shoppers can pay again after a dropped connection. 28 of 29 criteria proven · plan changed 7 times");
  expect(now.querySelector("a").getAttribute("href")).toBe("#plan");
  // What's left shows the meters in place of its list.
  expect(app.querySelector("#left .after")).toBeNull();
  expect(app.querySelectorAll("#left .plan-meter")).toHaveLength(2);
});

test("the title says every criterion is proven only when each is and the base is healthy, and counts waivers", async () => {
  const all = proven();
  let app = await first(all.document, all.plan);
  expect(title(app)).toBe("Every criterion is proven.");
  expect(app.querySelector("#plan .plan-open")).toBeNull();
  expect(app.querySelector("#plan .plan-calm").textContent).toBe("Nothing in the plan needs you right now.");

  const waived = proven(["A", "B"], { kinds: [...RECORD_KINDS, APP_KIND],
    evidence: { A: { app: { state: "waived", head: headOf("A"), reason: "No device." } }, B: { app: { state: "done", head: headOf("B") } } } });
  app = await first(waived.document, waived.plan);
  expect(title(app)).toBe("Every criterion is proven, 1 by waiver.");

  const broken = proven(["A", "B"], { extraSteps: [[40, "base.broken", null, { summary: "The base fails its checks" }]] });
  app = await first(broken.document, broken.plan);
  expect(title(app)).toBe("2 of 2 criteria proven.");
  expect(flags(app)).toEqual(["The base is broken"]);
  expect(flagWhy(app)).toEqual(["The base fails its checks"]);

  const repaired = proven(["A", "B"], { extraSteps: [[40, "base.broken", null], [41, "base.repaired", null]] });
  app = await first(repaired.document, repaired.plan);
  expect(title(app)).toBe("Every criterion is proven.");
  expect(flags(app)).toEqual([]);
});

test("each of the eight flags shows on its own, in order when several hold, and nothing needs you otherwise", async () => {
  const cases = [];
  // 2. The last base event is a break.
  cases.push(["base", proven(["A"], { extraSteps: [[40, "base.broken", null, { summary: "Base red" }]] }), ["The base is broken"], ["Base red"]]);
  // 3. Work in the record that the plan has no unit for, queued or planned.
  const stray = proven(["A"]);
  stray.document.state.queue.push(item("STRAY-1", "queued"));
  stray.document.state.planned.push({ subject: "STRAY-2", title: "Planned only", lane: "lane-1", stage: "building" });
  cases.push(["stray", stray, ["Work in the record isn't in the plan"], ["STRAY-1, STRAY-2"]]);
  // 4. A finish line only the plan has, and one only the record has.
  const lines = proven(["A"]);
  lines.document.state.finish = [{ id: "landed", text: "Landed", met: false }, { id: "extra", text: "Record only", met: false }];
  cases.push(["finish", lines, ["The plan and the record disagree on finish lines"], ["Only in the plan: all. Only in the record: extra"]]);
  // 5. An explicit list that predates a criterion added after its line was written.
  const covers = proven(["A", "B"], { finish: [finishLine("listed", { criteria: ["A.1"], proofs: ["merged"] }, { at: minute(0) })],
    changes: [{ id: "c1", at: minute(30), kind: "added", subjects: ["B"], by: "lead", why: "Asked for." }] });
  cases.push(["covers", covers, ["A finish line no longer covers the plan"], ["Line listed omits B.1"]]);
  // 6. A change on a unit that is neither proven nor removed.
  const touched = proven(["A", "B"], { queue: [item("A", "merged"), item("B", "queued")],
    changes: [{ id: "c1", at: minute(30), kind: "changed", subjects: ["B"], criteria: ["B.1"], by: "lead", why: "Moved.", before: "old", after: "new" }] });
  cases.push(["touched", touched, ["A change touches open work"], ["B"]]);
  // 7. A change with no reason, named by its title.
  cases.push(["reason", proven(["A"], { changes: [{ id: "c1", at: minute(30), kind: "relaxed", subjects: ["A"], by: "lead", title: "Fewer retries" }] }),
    ["A change has no reason"], ["Fewer retries"]]);
  // 5 again, for a criterion a later change rewrote or relaxed that the list omits; 6 again, for a relaxed change and
  // a removal naming a unit still in the plan.
  for (const kind of ["changed", "relaxed"]) {
    const rewritten = proven(["A", "B"], { finish: [finishLine("listed", { criteria: ["A.1"], proofs: ["merged"] }, { at: minute(0) })],
      changes: [{ id: "c1", at: minute(30), kind, subjects: ["B"], criteria: ["B.1"], by: "lead", why: "Reworded." }] });
    cases.push([`covers ${kind}`, rewritten, ["A finish line no longer covers the plan"], ["Line listed omits B.1"]]);
  }
  for (const kind of ["relaxed", "removed"]) {
    cases.push([`touched ${kind}`, proven(["A", "B"], { queue: [item("A", "merged"), item("B", "queued")],
      changes: [{ id: "c1", at: minute(30), kind, subjects: ["B"], by: "lead", why: "Eased." }] }), ["A change touches open work"], ["B"]]);
  }
  // 8. A waived proof on a unit not yet proven.
  const waived = proven(["A", "B"], { kinds: [...RECORD_KINDS, APP_KIND], queue: [item("A", "merged"), item("B", "queued")],
    evidence: { A: { app: { state: "done", head: headOf("A") } }, B: { app: { state: "waived", head: headOf("B"), reason: "No device." } } } });
  cases.push(["waived", waived, ["A waived proof on a unit not yet proven"], ["B"]]);
  for (const [name, { document, plan }, expected, why] of cases) {
    const app = await first(document, plan);
    expect([name, ...flags(app)]).toEqual([name, ...expected]);
    expect([name, ...flagWhy(app)]).toEqual([name, ...why]);
  }

  // All of them at once read in the contract's order.
  const every = proven(["A", "B"], { kinds: [...RECORD_KINDS, APP_KIND], queue: [item("A", "merged"), item("B", "queued"), item("STRAY-1", "queued")],
    extraSteps: [[40, "base.broken", null]], evidence: { A: { app: { state: "done", head: headOf("A") } }, B: { app: { state: "waived", head: headOf("B"), reason: "No device." } } },
    finish: [finishLine("listed", { criteria: ["A.1"], proofs: ["merged"] })],
    changes: [{ id: "c1", at: minute(30), kind: "added", subjects: ["B"], by: "lead" }, { id: "c2", at: minute(31), kind: "changed", subjects: ["B"], by: "lead", why: "Moved." }] });
  every.document.state.finish = [];
  expect(flags(await first(every.document, every.plan))).toEqual(["The base is broken", "Work in the record isn't in the plan",
    "The plan and the record disagree on finish lines", "A finish line no longer covers the plan", "A change touches open work",
    "A change has no reason", "A waived proof on a unit not yet proven"]);

  // None of them: a line covering no criteria and one covering every criterion never raise flag 5, nor does an explicit
  // list written after the change that added what it omits, a clipped change's emptied reason is no missing reason,
  // and a waiver on a proven unit needs nothing.
  const calm = proven(["A", "B"], { kinds: [...RECORD_KINDS, APP_KIND],
    finish: [finishLine("landed", { criteria: [], proofs: ["merged"] }, { at: minute(0) }), finishLine("all", { criteria: "all", proofs: ["review"] }, { at: minute(0) }),
      finishLine("listed", { criteria: ["A.1"], proofs: ["merged"] }, { at: minute(60) })],
    evidence: { A: { app: { state: "waived", head: headOf("A"), reason: "No device." } }, B: { app: { state: "done", head: headOf("B") } } },
    changes: [{ id: "c1", at: minute(30), kind: "added", subjects: ["B"], by: "lead", why: "Asked for." },
      { id: "c2", at: minute(31), kind: "changed", subjects: ["A"], by: "lead", why: "Clarified." }] });
  const page = render(calm.document, calm.plan);
  let app = (await open(page))[0].app;
  expect(flags(app)).toEqual([]);
  expect(app.querySelector("#plan .plan-calm").textContent).toBe("Nothing in the plan needs you right now.");
  const clipped = structuredClone(block(page, "delivery-plan"));
  Object.assign(clipped.plan.changes[0], { why: "", title: "", clipped: true });
  clipped.clipped = { changes: 1, texts: 0 };
  const snaps = await open(render(calm.document, undefined, { planBlock: clipped }), { steps: [{ toggle: '.plan-fold[data-fold="changes"]' }] });
  app = snaps[1].app;
  expect(flags(app)).toEqual([]);
  // The page says what was clipped, and marks the change whose text went.
  expect(app.querySelector("#plan .plan-clipped").textContent).toBe("The plan was too large to show whole: the text of the 1 oldest change left out.");
  expect([...app.querySelectorAll("#plan .plan-change")].map((row) => row.querySelector(".pc-why").textContent)).toEqual(
    ["By lead · Clarified.", "By lead · text clipped"]);
  // A clipped change that never had a reason still has none.
  const reasonless = structuredClone(clipped);
  delete reasonless.plan.changes[0].why;
  const [missing] = await open(render(calm.document, undefined, { planBlock: reasonless }));
  expect(flags(missing.app)).toEqual(["A change has no reason"]);
  expect(flagWhy(missing.app)).toEqual(["c1"]);
  expect(missing.app.querySelectorAll("#plan .plan-change")[1].querySelector(".pc-why").textContent).toBe("By lead · No reason given · text clipped");
});

test("a finish line's meter shows whether the record met it, and every line only the record has", async () => {
  const kinds = [...RECORD_KINDS, APP_KIND];
  const { document, plan } = proven(["A", "B"], { kinds,
    finish: [finishLine("landed", { criteria: [], proofs: ["merged"] }), finishLine("checked", { criteria: "all", proofs: ["app"] })],
    evidence: { A: { app: { state: "done", head: headOf("A") } } } });
  // B owes no in-app check, so it is settled for B: the meter agrees with the title.
  plan.units[1].proofs = ["review", "handed", "merged"];
  plan.approved.snapshot.units[1].proofs = ["review", "handed", "merged"];
  document.state.finish = [{ id: "landed", text: "Landed", met: true }, { id: "checked", text: "Checked", met: false },
    { id: "extra", text: "A line the plan lacks", met: false }];
  const app = await first(document, plan);
  expect(title(app)).toBe("Every criterion is proven.");
  expect([...app.querySelectorAll("#left .plan-meter")].map((meter) => [meter.className, meter.textContent])).toEqual([
    ["plan-meter met", "Line landedMet2 of 2 units"], ["plan-meter", "Line checkedNot met2 of 2 criteria"],
    ["plan-meter", "A line the plan lacksNot metNot in the plan"]]);
  expect(app.querySelectorAll("#left .plan-meter")[2].querySelector(".pm-done").style.width).toBe("0%");
});

test("a change stops touching open work once the unit's review approves after it", async () => {
  const before = proven(["A"], { queue: [item("A", "queued")],
    changes: [{ id: "c1", at: minute(30), kind: "changed", subjects: ["A"], by: "lead", why: "Moved." }] });
  expect(flags(await first(before.document, before.plan))).toEqual(["A change touches open work"]);
  const after = proven(["A"], { queue: [item("A", "queued")], extraSteps: [[50, "approve", "A", { round: 2 }]],
    changes: [{ id: "c1", at: minute(30), kind: "changed", subjects: ["A"], by: "lead", why: "Moved." }] });
  const app = await first(after.document, after.plan);
  expect(flags(app)).toEqual([]);
  expect(openRows(app)).toEqual(["AWork ANext: Merged: not merged yet"]);
  // A removed unit is never open work.
  const removed = proven(["A", "B"], { queue: [item("A", "merged"), item("B", "queued")],
    changes: [{ id: "c1", at: minute(30), kind: "removed", subjects: ["B"], by: "operator", why: "Not needed." }] });
  removed.plan.units[1].removed = true;
  expect(flags(await first(removed.document, removed.plan))).toEqual([]);
});

test("record proofs follow the record's own shapes: hand-offs, stops, acceptance, batches, merges and reviews", async () => {
  const rows = [];
  const shape = async (name, document, plan, subject, expected, options) => {
    rows.push([name, proofs(await first(document, plan, options), subject), expected]);
  };
  const plan = (subjects, more = {}) => planFor(subjects.map((subject) => unit(subject)), { finish: [], ...more });

  // A hand-off after a merge supersedes it: handed again, not merged, and the new round reviews the new head.
  await shape("re-handed", record({ queue: [item("A", "queued", { head: headOf("A", 1) })], journal: journal([[10, "approve", "A"],
    [11, "candidate.handed", "A"], [12, "candidate.merged", "A"], [20, "approve", "A", { round: 2 }], [21, "candidate.handed", "A"]]) }), plan(["A"]), "A",
  { Review: "done: approved", "Handed off": "done: done", Merged: "open: not merged yet" });
  await shape("stopped", record({ queue: [item("A", "stopped")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"],
    [12, "candidate.stopped", "A"]]) }), plan(["A"]), "A", { Review: "done: approved", "Handed off": "open: stopped", Merged: "open: not merged yet" });
  await shape("pending", record({ queue: [item("A", "queued", { pending: true })], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"]]) }),
    plan(["A"]), "A", { Review: "done: approved", "Handed off": "open: waiting for the lead to accept", Merged: "open: not merged yet" });
  const batch = record({ queue: [item("A", "merged"), item("B", "merged")], journal: journal([[10, "approve", "A"], [11, "approve", "B"],
    [12, "candidate.handed", "A"], [13, "candidate.handed", "B"], [14, "check.started", null, { summary: "Checking A, B together" }],
    [15, "candidate.merged", "A"], [15, "candidate.merged", "B"]]) });
  for (const subject of ["A", "B"]) {
    await shape(`batch ${subject}`, batch, plan(["A", "B"]), subject, { Review: "done: approved", "Handed off": "done: done", Merged: "done: done" });
  }
  // A merge the runner could not verify stops the item and breaks the base: merged stays open until the record shows it.
  await shape("merged_unverified", record({ queue: [item("A", "stopped")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"],
    [12, "base.broken", "A", { summary: "merged_unverified: main is not the synced tree" }], [12, "candidate.stopped", "A"]]) }), plan(["A"]), "A",
  { Review: "done: approved", "Handed off": "open: stopped", Merged: "open: not merged yet" });
  // A run-backed unit reads its run's latest round, with the journal's verdicts laid over a round the run gives none.
  const runRecord = (stage = "merged") => record({ queue: [item("A", stage, { runId: "run-a" })], journal: journal([[10, "changes", "A"],
    [11, "approve", "A", { round: 2 }], [12, "candidate.handed", "A"], [13, "candidate.merged", "A"]]) });
  const run = (rounds) => ({ "run-a": { runId: "run-a", roundDetail: rounds } });
  await shape("run approves", runRecord(), plan(["A"]), "A", { Review: "done: approved", "Handed off": "done: done", Merged: "done: done" },
    { runs: run([{ round: 1, at: minute(10), outcome: "changes", verdicts: {} }, { round: 2, at: minute(11), outcome: "approve", verdicts: {} }]) });
  await shape("run asks for changes", runRecord(), plan(["A"]), "A", { Review: "open: not approved yet", "Handed off": "done: done", Merged: "done: done" },
    { runs: run([{ round: 3, at: minute(11), outcome: "changes", verdicts: { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "changes" } }]) });
  await shape("run approves another head", runRecord(), plan(["A"]), "A", { Review: "stale: approved for another head", "Handed off": "done: done", Merged: "done: done" },
    { runs: run([{ round: 2, at: minute(11), candidateRef: headOf("A", 9), outcome: "approve",
      verdicts: { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "approve" } }]) });
  // A run round that names no candidate counts only after the hand-off before the current one.
  const rehandedRun = record({ queue: [item("A", "queued", { runId: "run-a", head: headOf("A", 4) })], journal: journal([[10, "approve", "A"],
    [11, "candidate.handed", "A"], [12, "candidate.stopped", "A"], [20, "candidate.handed", "A"]]) });
  const approving = { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "approve" };
  await shape("run round before the last hand-off", rehandedRun, plan(["A"]), "A",
    { Review: "stale: approved for another head", "Handed off": "done: done", Merged: "open: not merged yet" },
    { runs: run([{ round: 1, at: minute(10), outcome: "approve", verdicts: approving }]) });
  await shape("run round after the last hand-off", rehandedRun, plan(["A"]), "A",
    { Review: "done: approved", "Handed off": "done: done", Merged: "open: not merged yet" },
    { runs: run([{ round: 2, at: minute(15), outcome: "approve", verdicts: approving }]) });
  // Verdicts the configuration names by the lens's name count as that lens's.
  await shape("run verdicts by lens name", runRecord(), plan(["A"]), "A", { Review: "done: approved", "Handed off": "done: done", Merged: "done: done" },
    { runs: run([{ round: 2, at: minute(11), outcome: "approve", verdicts: { Correctness: "approve", Testing: "approve" } }]) });
  // An approval recorded before the previous hand-off approved an older head: stale, from the journal alone.
  await shape("stale head", record({ queue: [item("A", "queued", { head: headOf("A", 2) })], journal: journal([[10, "approve", "A"],
    [11, "candidate.handed", "A"], [12, "candidate.stopped", "A"], [20, "candidate.handed", "A"]]) }), plan(["A"]), "A",
  { Review: "stale: approved for another head", "Handed off": "done: done", Merged: "open: not merged yet" });
  await shape("merged", record({ queue: [item("A", "merged")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"],
    [12, "candidate.merged", "A"]]) }), plan(["A"]), "A", { Review: "done: approved", "Handed off": "done: done", Merged: "done: done" });
  await shape("no review", record({ queue: [item("A", "queued")], journal: journal([[11, "candidate.handed", "A"]]) }), plan(["A"]), "A",
    { Review: "none: not recorded", "Handed off": "done: done", Merged: "open: not merged yet" });
  // Rounds the lane only reported carry no verdicts.
  await shape("reported rounds only", record({ queue: [item("A", "queued", { reportedRounds: 3 })], journal: journal([[11, "candidate.handed", "A"]]) }),
    plan(["A"]), "A", { Review: "none: not recorded", "Handed off": "done: done", Merged: "open: not merged yet" });
  await shape("one lens of two", record({ queue: [item("A", "merged")], journal: journal([[10, "review.round", "A", { verdicts: { "lens.outcome-correctness": "approve" } }],
    [11, "candidate.handed", "A"], [12, "candidate.merged", "A"]]) }), plan(["A"]), "A", { Review: "open: not approved yet", "Handed off": "done: done", Merged: "done: done" });
  for (const [name, actual, expected] of rows) expect([name, actual]).toEqual([name, expected]);

  // The stale head's open row says what is missing.
  const app = await first(record({ queue: [item("A", "queued", { head: headOf("A", 2) })], journal: journal([[10, "approve", "A"],
    [11, "candidate.handed", "A"], [20, "candidate.handed", "A"]]) }), plan(["A"]));
  expect(openRows(app)).toEqual(["AWork ANext: Review: approved for another head"]);
  expect(title(app)).toBe("0 of 1 criteria proven.");
});

test("producer evidence counts at face value before hand-off and goes stale when the unit is handed off at a new head", async () => {
  const kinds = [...RECORD_KINDS, APP_KIND];
  const evidence = { A: { app: { state: "done", head: headOf("A"), items: ["checkout"] } } };
  const plan = planFor([unit("A")], { kinds, evidence });
  // Planned, not handed: the evidence counts as it stands, and the open row says it is before hand-off.
  let app = await first(record({ planned: [{ subject: "A", title: "Work A", lane: "lane-1", stage: "building" }], journal: journal([[10, "approve", "A"]]) }), plan);
  expect(proofs(app, "A")["In-app check"]).toBe("done: done");
  expect(openRows(app)).toEqual(["AWork ANext: Handed off: not handed off yet · before hand-off"]);
  // Handed off at the head the evidence names: done.
  app = await first(record({ queue: [item("A", "queued")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"]]) }), plan);
  expect(proofs(app, "A")["In-app check"]).toBe("done: done");
  // Handed off again at a new head: stale, and not done.
  app = await first(record({ queue: [item("A", "merged", { head: headOf("A", 3) })], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"],
    [12, "candidate.stopped", "A"], [13, "approve", "A", { round: 2 }], [14, "candidate.handed", "A"], [15, "candidate.merged", "A"]]) }), plan);
  expect(proofs(app, "A")).toEqual({ Review: "done: approved", "Handed off": "done: done", Merged: "done: done", "In-app check": "stale: for another head" });
  expect(openRows(app)).toEqual(["AWork ANext: In-app check: for another head"]);
  expect(title(app)).toBe("0 of 1 criteria proven.");
  // No entry at all is to do.
  app = await first(record({ queue: [item("A", "merged")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"]]) }),
    planFor([unit("A")], { kinds }));
  expect(proofs(app, "A")["In-app check"]).toBe("todo: to do");
});

test("real record shapes: a native review observation, then candidates the runner synced onto a moved base and merged", async () => {
  const folder = path.join(CORPUS, "plan-records");
  const kinds = [...RECORD_KINDS, APP_KIND];
  const shape = async (name) => {
    const document = readJson(path.join(folder, `${name}.json`));
    const heads = Object.fromEntries(document.state.queue.map((entry) => [entry.subject, entry.head]));
    const plan = planFor([unit("journal-sync"), unit("native-sync")], { kinds,
      evidence: Object.fromEntries(Object.entries(heads).map(([subject, head]) => [subject, { app: { state: "done", head } }])) });
    const app = await first(document, plan);
    return { app, document, rows: { "journal-sync": proofs(app, "journal-sync"), "native-sync": proofs(app, "native-sync") } };
  };
  // Handed off: the native unit's observation of its head is available; the other unit's review is its journal's.
  const handed = await shape("handed");
  expect(handed.document.reviewEvidence["native-sync"].unavailable).toBeUndefined();
  const waiting = { Review: "done: approved", "Handed off": "done: done", Merged: "open: not merged yet", "In-app check": "done: done" };
  expect(handed.rows).toEqual({ "journal-sync": waiting, "native-sync": waiting });
  // Synced onto the moved base: the runner moved syncedHead, the observation stays bound to the handed head it reviewed,
  // and both units keep their review and producer proofs, compared with the handed head.
  const synced = await shape("synced");
  const native = synced.document.state.queue.find((entry) => entry.subject === "native-sync");
  expect(native.stage).toBe("checked");
  expect(native.syncedHead === native.head || native.syncedHead === null).toBe(false);
  expect(synced.document.reviewEvidence["native-sync"].unavailable).toBeUndefined();
  expect(synced.document.reviewEvidence["native-sync"].candidateRef).toBe(native.head);
  expect(synced.rows["native-sync"]).toEqual(waiting);
  const landed = { Review: "done: approved", "Handed off": "done: done", Merged: "done: done", "In-app check": "done: done" };
  expect(synced.rows["journal-sync"]).toEqual(landed);
  // A live push that hands the native unit off again at a new head: the observation the page holds is of the old head,
  // so the review falls to the journal, whose approval came before the previous hand-off.
  const handedDocument = readJson(path.join(folder, "handed.json"));
  const rehanded = structuredClone(handedDocument);
  const moved = rehanded.state.queue.find((entry) => entry.subject === "native-sync");
  moved.head = headOf("native-sync", 5);
  const last = rehanded.journal[rehanded.journal.length - 1];
  rehanded.journal.push({ id: last.id + 1, at: last.at, kind: "candidate.handed", lane: moved.lane, subject: "native-sync", summary: "Handed off again" });
  delete rehanded.reviewEvidence;
  const heads = Object.fromEntries(handedDocument.state.queue.map((entry) => [entry.subject, entry.head]));
  const livePlan = planFor([unit("journal-sync"), unit("native-sync")], { kinds,
    evidence: Object.fromEntries(Object.entries(heads).map(([subject, head]) => [subject, { app: { state: "done", head } }])) });
  const pushed = await open(render(handedDocument, livePlan), { store: { read: "pending" }, steps: [{ push: rehanded }] });
  expect(proofs(pushed[0].app, "native-sync").Review).toBe("done: approved");
  expect(proofs(pushed[1].app, "native-sync")).toEqual({ Review: "stale: approved for another head", "Handed off": "done: done",
    Merged: "open: not merged yet", "In-app check": "stale: for another head" });
  // An available observation of the head that does not approve, or lacks a required lens, leaves the review open.
  const handedPage = render(handedDocument, livePlan);
  const observation = block(handedPage, "delivery-reviews");
  const rewritten = (change) => {
    const copy = structuredClone(observation);
    change(copy["native-sync"].roundDetail.at(-1));
    return handedPage.replace(`<script type="application/json" id="delivery-reviews">${embedded(observation)}</script>`,
      `<script type="application/json" id="delivery-reviews">${embedded(copy)}</script>`);
  };
  for (const [name, change] of [["asks for changes", (round) => { round.outcome = "changes"; round.verdicts["lens.adversarial-testing"] = "changes"; }],
    ["one lens missing", (round) => { delete round.verdicts["lens.adversarial-testing"]; }]]) {
    const changed = rewritten(change);
    expect(changed === handedPage).toBe(false);
    const [shown] = await open(changed);
    expect([name, proofs(shown.app, "native-sync").Review]).toEqual([name, "open: not approved yet"]);
  }
  // A pushed record whose observation is unavailable, or whose digest is not the one the page holds, is no
  // observation: the journal's latest round answers, here asking for changes.
  const disagreeing = (evidence) => {
    const pushedRecord = structuredClone(handedDocument);
    const previous = pushedRecord.journal[pushedRecord.journal.length - 1];
    pushedRecord.journal.push({ id: previous.id + 1, at: previous.at, kind: "review.round", lane: "lane-2", subject: "native-sync", summary: "native-sync: review round 3",
      review: { round: 3, startedAt: previous.at, finishedAt: previous.at, verdicts: Object.fromEntries(LENSES.map((lens) => [lens, "changes"])), findings: { P0: 0, P1: 1, P2: 0, P3: 0 } } });
    pushedRecord.reviewEvidence["native-sync"] = evidence(pushedRecord.reviewEvidence["native-sync"]);
    return pushedRecord;
  };
  for (const [name, evidence, expected] of [["consistent", (entry) => entry, "done: approved"],
    ["another digest", (entry) => ({ ...entry, digest: "0".repeat(64) }), "open: not approved yet"],
    ["unavailable", (entry) => ({ ...entry, unavailable: true }), "open: not approved yet"]]) {
    const live = await open(handedPage, { store: { read: "pending" }, steps: [{ push: disagreeing(evidence) }] });
    expect([name, proofs(live[1].app, "native-sync").Review]).toEqual([name, expected]);
  }
  // Merged: both stay proven.
  const merged = await shape("merged");
  expect(merged.rows).toEqual({ "journal-sync": landed, "native-sync": landed });
  expect(title(merged.app)).toBe("Every criterion is proven.");
});

test("a live page redraws the chapter from each pushed record, and says when the plan itself changed", async () => {
  const { document, plan } = recordOnlyDelivery();
  const page = render(document, plan);
  const landed = structuredClone(document);
  landed.state.queue[3].stage = "merged";
  landed.journal.push({ id: 12, at: minute(30), kind: "review.round", lane: "lane-1", subject: "PAY-4", summary: "PAY-4: review round 2",
    review: { round: 2, startedAt: minute(30), finishedAt: minute(30), verdicts: Object.fromEntries(LENSES.map((lens) => [lens, "approve"])), findings: { P0: 0, P1: 0, P2: 0, P3: 0 } } },
  { id: 13, at: minute(31), kind: "candidate.merged", lane: "lane-1", subject: "PAY-4", summary: "Merged PAY-4" });
  const replanned = structuredClone(landed);
  replanned.journal.push({ id: 14, at: minute(40), kind: "decision.recorded", lane: null, subject: null,
    summary: "Plan updated: 2026-10-30T13:00:00Z; added PAY-5" });
  const snaps = await open(page, { store: { read: "pending" }, steps: [{ push: landed }, { push: replanned }] });
  const [before, merged, notice] = snaps.map((snapshot) => snapshot.app);
  expect(title(before)).toBe("4 of 6 criteria proven.");
  expect(before.querySelector(".plan-reload")).toBeNull();
  expect(title(merged)).toBe("Every criterion is proven.");
  expect(merged.querySelector("#plan .plan-open")).toBeNull();
  expect(merged.querySelector("#now .plan-now").textContent).toContain("6 of 6 criteria proven");
  expect(merged.querySelector(".plan-reload")).toBeNull();
  expect(notice.querySelector("#plan .plan-title .plan-reload").textContent).toBe("The plan changed. Reload to see it.");
  // A page rendered after that entry, from the plan it names, shows no notice.
  const [again] = await open(render(replanned, { ...plan, updated: "2026-10-30T13:00:00Z" }));
  expect(again.app.querySelector(".plan-reload")).toBeNull();
  // Only the latest "Plan updated:" decision counts: here it names the plan this page holds. Another kind of entry
  // saying the same words is no plan write.
  const settled = structuredClone(replanned);
  settled.journal.push({ id: 15, at: minute(41), kind: "decision.recorded", lane: null, subject: null, summary: `Plan updated: ${plan.updated}; reverted` },
    { id: 16, at: minute(42), kind: "blocker.recorded", lane: null, subject: null, summary: "Plan updated: 2026-10-30T14:00:00Z; not a plan write" });
  const later = await open(page, { store: { read: "pending" }, steps: [{ push: replanned }, { push: settled }] });
  expect(later[1].app.querySelector(".plan-reload")).toBeTruthy();
  expect(later[2].app.querySelector(".plan-reload")).toBeNull();
});

// A record the page cannot draw clears the chapter with every other, rather than leave a stale plan beside it.
test("a record the page cannot draw leaves the plan chapter empty beside the message", async () => {
  const { document, plan } = recordOnlyDelivery();
  const page = render(document, plan).replace(/<script type="application\/json" id="delivery-state">[^<]*<\/script>/,
    '<script type="application/json" id="delivery-state">"not a record"</script>');
  const [shown] = await open(page);
  expect(shown.app.querySelector("#now h1").textContent).toBe("The delivery record could not be read.");
  expect(shown.app.querySelector("#plan .inner").children).toHaveLength(0);
});

test("the units switch shows the plan as approved and as amended, with a removed unit marked", async () => {
  const { document, plan } = inAppDelivery();
  plan.units[3].removed = true;
  plan.changes.push({ id: "c8", at: minute(320), kind: "removed", subjects: ["CART-104"], by: "operator", why: "Folded into CART-103." });
  const page = render(document, plan);
  const snaps = await open(page, { steps: [{ toggle: '.plan-fold[data-fold="units"]' }, { click: ".plan-switch button", index: 1 },
    { click: ".plan-switch button", index: 0 }] });
  const rows = (app) => [...app.querySelectorAll(".plan-all .plan-unit")].map((row) => [row.querySelector("[data-ticket]").getAttribute("data-ticket"),
    row.classList.contains("removed")]);
  const amended = snaps[1].app;
  expect(title(amended)).toBe("27 of 28 criteria proven, 1 by waiver.");
  expect(rows(amended)).toHaveLength(20);
  expect(rows(amended)[3]).toEqual(["CART-104", true]);
  expect(amended.querySelectorAll(".plan-all .pu-removed")).toHaveLength(1);
  expect(amended.querySelector('.plan-fold[data-fold="units"] summary').textContent).toBe("All units20");
  const approved = snaps[2].app;
  expect([...approved.querySelectorAll(".plan-switch button")].map((button) => button.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
  expect(rows(approved).map((row) => row[0])).toEqual(plan.approved.snapshot.units.map((entry) => entry.subject));
  expect(rows(approved).filter((row) => row[1])).toEqual([["CART-104", true]]);
  expect(approved.querySelector(".plan-switch .ps-when").textContent).toBe("Approved 6 hours ago · Both lenses aligned");
  expect(rows(snaps[3].app)).toEqual(rows(amended));
});

test("each fold stays open across redraws and is remembered, with or without storage", async () => {
  const { document, plan } = inAppDelivery();
  const page = render(document, plan);
  const names = ["units", "changes", "waived", "approvals", "out", "outcome"];
  for (const storage of [true, false]) {
    const steps = [...names.map((name) => ({ toggle: `.plan-fold[data-fold="${name}"]` })), { push: document }];
    // Storage needs an address; the page is served at its /view/ address, as the live server serves it.
    const snaps = await open(page, { store: { read: "pending" }, ...(storage ? { rendered: page } : { storage: false }), steps });
    const last = snaps[snaps.length - 1].app;
    expect([storage, names.map((name) => last.querySelector(`.plan-fold[data-fold="${name}"]`).open)]).toEqual([storage, names.map(() => true)]);
    if (storage) expect(JSON.parse(snaps.storage["delivery-views:test:plan-folds"]).sort()).toEqual([...names].sort());
    else expect(snaps.storage).toBeNull();
  }
});

test("folds stored for this page start open on the next load", async () => {
  const { document, plan } = inAppDelivery();
  const page = render(document, plan);
  const [shown] = await open(page, { rendered: page, stored: { "delivery-views:test:plan-folds": JSON.stringify(["changes", "out"]) } });
  expect([...shown.app.querySelectorAll(".plan-fold")].filter((fold) => fold.open).map((fold) => fold.getAttribute("data-fold"))).toEqual(["changes", "out"]);
});

test("each stop on the line opens its unit in the chapter, in the open list or in the folded units", async () => {
  const { document, plan } = inAppDelivery();
  const page = render(document, plan);
  let snaps = await open(page, { steps: [{ click: '.stop-btn[data-subject="CART-105"]' }, { click: "#detail .d-plan" }] });
  expect(snaps[1].app.querySelector("#detail .d-plan").textContent).toBe("In the plan: next: Merged, not merged yet");
  expect(snaps[2].focused).toBe("li");
  expect(snaps[2].app.querySelector('.plan-fold[data-fold="units"]').open).toBe(false);
  snaps = await open(page, { steps: [{ click: '.stop-btn[data-subject="CART-101"]' }, { click: "#detail .d-plan" }] });
  expect(snaps[1].app.querySelector("#detail .d-plan").textContent).toBe("In the plan: proven");
  expect(snaps[2].focused).toBe("div");
  expect(snaps[2].app.querySelector('.plan-fold[data-fold="units"]').open).toBe(true);
  // From the plan as approved, a unit added since is found once the switch is back on the plan as amended.
  snaps = await open(page, { steps: [{ toggle: '.plan-fold[data-fold="units"]' }, { click: ".plan-switch button", index: 1 },
    { click: '.stop-btn[data-subject="CART-113"]' }, { click: "#detail .d-plan" }] });
  expect([...snaps[4].app.querySelectorAll(".plan-switch button")].map((button) => button.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
  expect(snaps[4].focused).toBe("div");
  expect(snaps[4].app.querySelectorAll(".plan-all .plan-unit")).toHaveLength(20);
  // A stop the plan has no unit for has no link.
  const stray = structuredClone(document);
  stray.state.queue.push(item("STRAY-1", "queued"));
  snaps = await open(render(stray, plan), { steps: [{ click: '.stop-btn[data-subject="STRAY-1"]' }] });
  expect(snaps[1].app.querySelector("#detail .d-plan")).toBeNull();
});

test("on a phone each unit is a card with labelled proof pills, and both themes colour the chapter from their tokens", async () => {
  const css = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "line-map.css"), "utf8");
  const section = css.slice(css.indexOf("/* The plan:"));
  const phone = section.slice(section.indexOf("@media (max-width: 640px)"));
  expect(phone).toContain(".plan-unit { grid-template-columns: 1fr;");
  expect(phone).toContain(".plan-pill .pp-kind { display: inline;");
  expect(phone).toContain(".plan-grid-head { display: none; }");
  expect(section).toContain(".plan-pill .pp-kind { display: none; }");
  // No colour of its own: every one is a theme token that the light and both dark rules define.
  expect(section).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
  const tokens = [...new Set([...section.matchAll(/var\(--([a-z0-9-]+)/g)].map((match) => match[1]))].filter((token) => token !== "cols");
  const rule = (selector) => css.slice(css.indexOf(selector), css.indexOf("}", css.indexOf(selector)));
  for (const scope of [":root {", ':root:not([data-theme="light"]) {', ':root[data-theme="dark"] {']) {
    const defined = rule(scope);
    for (const token of tokens.filter((name) => !["display", "text", "ease", "mono"].includes(name))) expect([scope, token, defined.includes(`--${token}:`)]).toEqual([scope, token, true]);
  }
  // The grid has one column per declared proof kind, and every pill names its kind for the phone layout.
  const { document, plan } = recordOnlyDelivery();
  const app = await first(document, plan);
  const row = app.querySelector(".plan-all .plan-unit");
  expect(row.style.getPropertyValue("--cols")).toBe("3");
  expect([...row.querySelectorAll(".plan-pill .pp-kind")].map((node) => node.textContent)).toEqual(["Review", "Handed off", "Merged"]);
  const inApp = inAppDelivery();
  expect((await first(inApp.document, inApp.plan)).querySelector(".plan-all .plan-unit").style.getPropertyValue("--cols")).toBe("4");
});

test("plan text never becomes markup, and a ref becomes a link only through the configured link rule", async () => {
  const hostile = '</script><img src=x onerror=alert(1)> "quoted"';
  const subject = 'X"]</script><img src=x onerror=alert(2)>';
  const document = record({ queue: [item(subject, "queued")], journal: journal([[10, "approve", subject], [11, "candidate.handed", subject]]) });
  const plan = planFor([unit(subject, { criteria: [{ id: "x.1", text: hostile }], title: hostile })], {
    changes: [{ id: "c1", at: minute(30), kind: "changed", subjects: [subject], criteria: ["x.1"], by: "lead", why: hostile, title: hostile,
      before: hostile, after: `${hostile} now`, source: hostile, ref: "javascript:alert(3)" }] });
  const page = render(document, plan);
  expect(page).not.toContain("<img src=x");
  const snaps = await open(page, { steps: [{ toggle: '.plan-fold[data-fold="changes"]' }, { click: ".stop-btn" }, { click: "#detail .d-plan" }] });
  const app = snaps[1].app;
  expect(app.querySelectorAll("img, script")).toHaveLength(0);
  expect(app.querySelector(".plan-change .pc-title").textContent).toBe(hostile);
  expect(app.querySelector(".plan-change .pc-why").textContent).toContain(hostile);
  expect(app.querySelector(".plan-change .redline").textContent).toBe(`${hostile} now`);
  expect([...app.querySelectorAll(".plan-change .redline ins")].map((node) => node.textContent).join("")).toBe(" now");
  expect(app.querySelectorAll(".plan-change .redline del")).toHaveLength(0);
  expect(app.querySelector(".plan-change .pc-ref").textContent).toBe("javascript:alert(3)");
  expect(app.querySelectorAll("#plan a[href^='javascript'], #plan [onerror]")).toHaveLength(0);
  expect(app.querySelector(".plan-all .pu-criteria li").textContent).toBe(`x.1 ${hostile}`);
  // The unit is found by its subject through the page's own map, so the stop's link reaches it.
  expect(snaps[3].focused).toBe("li");
  // A ref the renderer linked, a change request on the record, opens as that link does on the line.
  const linked = record({ queue: [item("PAY-9", "queued", { changeRequest: "#77" })], journal: journal([[11, "candidate.handed", "PAY-9"]]) });
  const linkedPage = pageWithRuns(linked, {}, { config: { ...CONFIG, forges: [{ name: "Forge", repository: "acme/shop", pullUrl: "https://forge.example/{repository}/pull/{number}", commitUrl: "https://forge.example/{repository}/commit/{sha}" }] },
    plan: planFor([unit("PAY-9")], { changes: [{ id: "c1", at: minute(30), kind: "changed", subjects: ["PAY-9"], by: "lead", why: "Moved.", ref: "#77" }] }) });
  const [shown] = await open(linkedPage);
  expect(shown.app.querySelector(".plan-change .pc-why a").getAttribute("href")).toBe("https://forge.example/acme/shop/pull/77");
});
