// The plan chapter: is the work still what was agreed, and what proves it? Every test fixes the clock and the time
// zone and starts with empty storage.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, REPLAYED_RUNS, ROOT, block, embedded, expect, openPage, pageWithRuns, planEnd, readJson, renderPage } from "./support.mjs";
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
/** Each owed proof of a unit in the all-units grid, by its kind's label: its state class and its words. */
function proofs(app, subject) {
  const row = [...app.querySelectorAll("#plan .plan-all .plan-unit")]
    .find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === subject);
  return Object.fromEntries([...row.querySelectorAll(".plan-proof")]
    .map((line) => [line.querySelector(".pp-kind").textContent, `${line.className.replace("plan-proof ", "")}: ${line.querySelector(".pp-state").textContent}`]));
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

test("a record naming a plan with no plan.json flags the chapter as missing, and names the plan", async () => {
  const { document } = recordOnlyDelivery();
  const [shown] = await open(render(document, undefined, { planBlock: { absent: "docs/plans/pay.md" } }));
  expect(rail(shown.app)).toEqual(CHAPTER_ORDER);
  expect(flags(shown.app)).toEqual(["The plan chapter is missing"]);
  expect(flagWhy(shown.app)).toEqual(["docs/plans/pay.md is the plan on file, and nothing has written plan.json for this page."]);
  expect(shown.app.querySelector(".plan-now")).toBeNull();
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
    ["kicker", "title plan-title", "say plan-outcome", "plan-needs", "plan-open", "plan-folds"]);
  expect(title(app)).toBe("28 of 29 criteria proven, 1 by waiver.");
  expect(app.querySelector("#plan .plan-outcome").textContent).toBe("Shoppers can pay again after a dropped connection.");
  expect(flags(app)).toEqual(["A finish line no longer covers the plan", "A change touches open work"]);
  expect(openRows(app)).toEqual(["CART-105Cart step 5Merged: not merged yet"]);
  expect(app.querySelector("#plan .plan-meter")).toBeNull();
  expect([...app.querySelectorAll("#left .plan-meter")].map((meter) => meter.textContent)).toEqual(
    ["Every cart criterion mergedNot metMerged: 13 of 14 criteria", "Checked in the appNot metIn-app check: 27 of 29 criteria, 1 more by waiver"]);
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
    ["plan-meter met", "Line landedMetMerged: 2 of 2 units"], ["plan-meter", "Line checkedNot marked metIn-app check: 2 of 2 criteria"],
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
  expect(openRows(app)).toEqual(["AWork AMerged: not merged yet"]);
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
  // A merge recorded outside the queue can leave the flag set; what merged is past waiting.
  await shape("merged, flag left set", record({ queue: [item("A", "merged", { pending: true })], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"],
    [12, "candidate.merged", "A"]]) }), plan(["A"]), "A", { Review: "done: approved", "Handed off": "done: done", Merged: "done: done" });
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
  expect(openRows(app)).toEqual(["AWork AReview: approved for another head"]);
  expect(title(app)).toBe("0 of 1 criteria proven.");
});

test("producer evidence counts at face value before hand-off and goes stale when the unit is handed off at a new head", async () => {
  const kinds = [...RECORD_KINDS, APP_KIND];
  const evidence = { A: { app: { state: "done", head: headOf("A"), items: ["checkout"] } } };
  const plan = planFor([unit("A")], { kinds, evidence });
  // Planned, not handed: the evidence counts as it stands, and the open row says it is before hand-off.
  let app = await first(record({ planned: [{ subject: "A", title: "Work A", lane: "lane-1", stage: "building" }], journal: journal([[10, "approve", "A"]]) }), plan);
  expect(proofs(app, "A")["In-app check"]).toBe("done: done");
  expect(openRows(app)).toEqual(["AWork AHanded off: not handed off yet · before hand-off"]);
  // Handed off at the head the evidence names: done.
  app = await first(record({ queue: [item("A", "queued")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"]]) }), plan);
  expect(proofs(app, "A")["In-app check"]).toBe("done: done");
  // Handed off again at a new head: stale, and not done.
  app = await first(record({ queue: [item("A", "merged", { head: headOf("A", 3) })], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"],
    [12, "candidate.stopped", "A"], [13, "approve", "A", { round: 2 }], [14, "candidate.handed", "A"], [15, "candidate.merged", "A"]]) }), plan);
  expect(proofs(app, "A")).toEqual({ Review: "done: approved", "Handed off": "done: done", Merged: "done: done", "In-app check": "stale: for another head" });
  expect(openRows(app)).toEqual(["AWork AIn-app check: for another head"]);
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

/**
 * A live delivery whose plan changes: `page` holds `plan`, `after` is the page the server renders next from `changed`,
 * and `replanned` is the pushed record whose decision names it.
 */
function replanning() {
  const { document, plan } = recordOnlyDelivery();
  const changed = { ...plan, updated: "2026-10-30T13:00:00Z", outcome: { ...plan.outcome, summary: "The outcome the changed plan states." } };
  const replanned = structuredClone(document);
  replanned.journal.push({ id: 14, at: minute(40), kind: "decision.recorded", lane: null, subject: null, summary: `Plan updated: ${changed.updated}; a unit changed` });
  return { document, plan, page: render(document, plan), changed, replanned, after: render(replanned, changed) };
}
const outcome = (app) => app.querySelector("#plan .plan-outcome").textContent;
const planNote = (app) => app.querySelector("#plan .plan-title .plan-reload")?.textContent ?? null;
const OUT_OF_DATE = "The plan shown is out of date.";
const RELOAD = "The plan changed. Reload to see it.";
/** A live page, its timers a hundred times quicker. */
function live(page, rendered, steps, extra = {}) {
  return open(page, { store: { read: "pending" }, quicker: 100, rendered, steps, ...extra });
}
const SETTLE = { asked: 1 };
/** Time for an ask that should not come. */
const QUIET = { wait: 60 };

test("a live page shows a changed plan with no reload, and no note on the way", async () => {
  const { page, plan, replanned, after } = replanning();
  const snaps = await live(page, after, [{ push: replanned }, SETTLE]);
  expect(outcome(snaps[0].app)).toBe(plan.outcome.summary);
  // Asked, perhaps not answered: nobody is told to do anything.
  expect(planNote(snaps[1].app)).toBeNull();
  expect(outcome(snaps[2].app)).toBe("The outcome the changed plan states.");
  expect(planNote(snaps[2].app)).toBeNull();
  expect(snaps[2].text).not.toContain("Reload to see it");
  expect(snaps.fetchOptions[0].cache).toBe("no-store");
  // Holding the plan the record names, it asks no more, however often the record moves.
  const settled = await live(page, after, [{ push: replanned }, SETTLE, { push: replanned }, QUIET]);
  expect(settled.fetched).toHaveLength(1);
});

test("a live page keeps asking until the server has rendered the plan the record names", async () => {
  const { page, plan, replanned, after } = replanning();
  // The server pushes a record before it renders, so the first answers still hold the old plan.
  let asked = 0;
  const snaps = await live(page, () => (asked++ < 2 ? page : after), [{ push: replanned }, { asked: 3 }]);
  expect(outcome(snaps[1].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[1].app)).toBeNull();
  expect(outcome(snaps[2].app)).toBe("The outcome the changed plan states.");
  expect(planNote(snaps[2].app)).toBeNull();
  expect(snaps.fetched).toHaveLength(3);
});

test("a render that never holds the named plan ends in the out-of-date note, until one does", async () => {
  const { page, plan, replanned, after } = replanning();
  let asked = 0;
  const snaps = await live(page, () => (asked++ < 16 ? page : after), [{ push: replanned, asked: 14 }, { asked: 15 }, { asked: 16 }, { asked: 17 }]);
  // Quiet while it may yet be rendered, then saying so, and still asking.
  expect(planNote(snaps[1].app)).toBeNull();
  expect(outcome(snaps[3].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[3].app)).toBe(OUT_OF_DATE);
  expect(outcome(snaps[4].app)).toBe("The outcome the changed plan states.");
  expect(planNote(snaps[4].app)).toBeNull();
});

for (const [name, answered] of [["can't be fetched", () => new Error("connection refused")],
  // Refused, even with a plan in the body: only a page the server stands behind counts.
  ["is refused", (after) => ({ refuse: 503, body: after })]]) {
  test(`a plan that ${name} leaves the current plan on screen, saying it is out of date`, async () => {
    const { page, plan, replanned, after } = replanning();
    const snaps = await live(page, () => answered(after), [{ push: replanned }, SETTLE]);
    expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
    expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
    expect(snaps[2].text).not.toContain("Reload to see it");
    expect(snaps[2].app.querySelectorAll('#plan .plan-fold[data-fold="units"] .plan-unit').length).toBeGreaterThan(0);
  });
}

test("a plan that fails the server's validation leaves the current plan on screen, saying it is out of date", async () => {
  const { page, plan, replanned } = replanning();
  // The answer comes from the renderer's own validator, reading an invalid plan.json.
  const invalid = renderPage(replanned, { config: CONFIG, plan: { ...plan, units: "not units" } });
  expect(typeof block(invalid, "delivery-plan").unreadable).toBe("string");
  const snaps = await live(page, invalid, [{ push: replanned }, SETTLE]);
  expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(snaps[2].text).not.toContain("couldn't be read");
});

const PLAN_BLOCK = /(<script type="application\/json" id="delivery-plan">)[^<]*(<\/script>)/;
for (const [name, answered] of [
  ["no plan at all", (after) => after.replace(PLAN_BLOCK, "")],
  ["a plan block that isn't JSON", (after) => after.replace(PLAN_BLOCK, "$1{nope$2")],
  ["a plan with no updated value", (after) => after.replace(PLAN_BLOCK, '$1{"plan":{}}$2')],
  ["a plan that can't be drawn", (after, changed) => after.replace(PLAN_BLOCK, (_, opening, closing) => `${opening}${JSON.stringify({ plan: { ...changed, units: [{ ...changed.units[0], proofs: [null] }] } })}${closing}`)],
]) {
  test(`a page answering with ${name} keeps the current plan, saying it is out of date`, async () => {
    const { page, plan, changed, replanned, after } = replanning();
    const snaps = await live(page, answered(after, changed), [{ push: replanned }, SETTLE]);
    expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
    expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
    expect(snaps[2].app.querySelector("#now h1")?.textContent ?? "").not.toContain("could not be read");
  });
}

test("a failed refresh lands on a later try, and the note goes", async () => {
  const { page, plan, replanned, after } = replanning();
  let asked = 0;
  const snaps = await live(page, () => (asked++ === 0 ? new Error("down") : after), [{ push: replanned }, { asked: 1 }, { asked: 2 }], { quicker: 500 });
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[3].app)).toBeNull();
  expect(outcome(snaps[3].app)).toBe("The outcome the changed plan states.");
});

test("a live page holding a later plan than the record names keeps asking, then rests when the server goes on holding it", async () => {
  const { document, plan } = recordOnlyDelivery();
  // The plan file is written before its journal entry, so a render can hold a plan the record doesn't name yet.
  const lagging = structuredClone(document);
  lagging.journal.push({ id: 14, at: minute(40), kind: "decision.recorded", lane: null, subject: null, summary: "Plan updated: 2026-10-30T13:00:00Z; a unit changed" });
  const held = render(document, { ...plan, updated: "2026-10-30T14:00:00Z" });
  const snaps = await live(held, held, [{ push: lagging }, { asked: 15 }, { push: lagging }, QUIET]);
  expect(snaps.fetched).toHaveLength(15);
  expect(planNote(snaps[2].app)).toBeNull();
  expect(planNote(snaps[4].app)).toBeNull();
});

test("a plan a server still rendering hasn't reached yet is taken when it arrives, however early the page asked", async () => {
  const { document, plan } = recordOnlyDelivery();
  const restored = structuredClone(document);
  restored.journal.push({ id: 14, at: minute(40), kind: "decision.recorded", lane: null, subject: null, summary: `Plan updated: ${plan.updated}; restored` });
  const later = { ...plan, updated: "2026-10-30T14:00:00Z", outcome: { ...plan.outcome, summary: "The later plan's outcome." } };
  const held = render(document, later);
  let asked = 0;
  const snaps = await live(held, () => (asked++ < 3 ? held : render(restored, plan)), [{ push: restored }, { asked: 3 }, { asked: 4 }]);
  expect(outcome(snaps[2].app)).toBe("The later plan's outcome.");
  expect(planNote(snaps[2].app)).toBeNull();
  expect(outcome(snaps[3].app)).toBe(plan.outcome.summary);
  expect(snaps.fetched).toHaveLength(4);
});

test("a name the page rested on is followed again when the record comes back to it", async () => {
  const { document, plan } = recordOnlyDelivery();
  const name = (record, id, stamp) => { const next = structuredClone(record); next.journal.push({ id, at: minute(40 + id), kind: "decision.recorded", lane: null, subject: null, summary: `Plan updated: ${stamp}; a change` }); return next; };
  const first = name(document, 14, "2026-10-30T13:00:00Z");
  const third = name(first, 15, "2026-10-30T15:00:00Z");
  const fourth = name(third, 16, "2026-10-30T13:00:00Z");
  const held = render(document, { ...plan, updated: "2026-10-30T14:00:00Z" });
  const latest = render(third, { ...plan, updated: "2026-10-30T15:00:00Z" });
  const restored = render(fourth, { ...plan, updated: "2026-10-30T13:00:00Z", outcome: { ...plan.outcome, summary: "The restored outcome." } });
  let asked = 0;
  const snaps = await live(held, () => (asked++ < 15 ? held : asked === 16 ? latest : restored),
    [{ push: first }, { asked: 15 }, { push: third }, { asked: 16 }, { push: fourth }, { asked: 17 }]);
  expect(outcome(snaps[6].app)).toBe("The restored outcome.");
});

test("once out of date, an answer holding the plan in use says it is current after all", async () => {
  const { document, plan } = recordOnlyDelivery();
  const lagging = structuredClone(document);
  lagging.journal.push({ id: 14, at: minute(40), kind: "decision.recorded", lane: null, subject: null, summary: "Plan updated: 2026-10-30T13:00:00Z; a unit changed" });
  const held = render(document, { ...plan, updated: "2026-10-30T14:00:00Z" });
  let asked = 0;
  const snaps = await live(held, () => (asked++ === 0 ? new Error("down") : held), [{ push: lagging }, { asked: 1 }, { asked: 2 }], { quicker: 500 });
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(planNote(snaps[3].app)).toBeNull();
});

test("a later plan taken after a failed ask shows no note", async () => {
  const { page, changed, replanned } = replanning();
  const newer = render(replanned, { ...changed, updated: "2026-10-30T14:00:00Z", outcome: { ...changed.outcome, summary: "A later outcome still." } });
  let asked = 0;
  const snaps = await live(page, () => (asked++ === 0 ? new Error("down") : newer), [{ push: replanned }, { asked: 1 }, { asked: 2 }], { quicker: 500 });
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(outcome(snaps[3].app)).toBe("A later outcome still.");
  expect(planNote(snaps[3].app)).toBeNull();
});

test("a later plan that can't be drawn goes back too, with the note", async () => {
  const { page, plan, changed, replanned } = replanning();
  const broken = render(replanned, undefined, { planBlock: { plan: { ...changed, updated: "2026-10-30T14:00:00Z", units: [{ ...changed.units[0], removed: true, proofs: [null] }, ...changed.units.slice(1)] } } });
  const snaps = await live(page, broken, [{ push: replanned }, SETTLE, QUIET]);
  expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  // It goes back to asking at the slow pace, which is longer than this test waited.
  expect(snaps.fetched).toHaveLength(1);
});

test("each newly named plan gets its own patience", async () => {
  const { page, replanned } = replanning();
  const again = structuredClone(replanned);
  again.journal.push({ id: 15, at: minute(41), kind: "decision.recorded", lane: null, subject: null, summary: "Plan updated: 2026-10-30T13:30:00Z; another change" });
  const snaps = await live(page, page, [{ push: replanned }, { asked: 14 }, { push: again }, { asked: 28 }, { asked: 29 }]);
  expect(planNote(snaps[4].app)).toBeNull();
  expect(planNote(snaps[5].app)).toBe(OUT_OF_DATE);
});

test("a live page takes an earlier plan when the server holds the one the record names", async () => {
  const { document, plan } = recordOnlyDelivery();
  // A restored file keeps its old stamp, or a producer's clock is behind: the record still names what is current.
  const restored = structuredClone(document);
  restored.journal.push({ id: 14, at: minute(40), kind: "decision.recorded", lane: null, subject: null, summary: `Plan updated: ${plan.updated}; restored` });
  const later = { ...plan, updated: "2026-10-30T14:00:00Z", outcome: { ...plan.outcome, summary: "The later plan's outcome." } };
  const snaps = await live(render(document, later), render(restored, plan), [{ push: restored }, SETTLE]);
  expect(outcome(snaps[0].app)).toBe("The later plan's outcome.");
  expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[2].app)).toBeNull();
});

test("a newly named plan clears the note and asks again at once, not after the long wait", async () => {
  const { page, plan, changed, replanned } = replanning();
  const again = structuredClone(replanned);
  again.journal.push({ id: 15, at: minute(41), kind: "decision.recorded", lane: null, subject: null, summary: "Plan updated: 2026-10-30T13:30:00Z; another change" });
  const second = render(again, { ...changed, updated: "2026-10-30T13:30:00Z", outcome: { ...plan.outcome, summary: "The second change's outcome." } });
  let asked = 0;
  // At this speed a new ask comes in 50ms and the long wait takes 1500ms, so the last step ends inside the long wait.
  const snaps = await live(page, () => (asked++ === 0 ? new Error("down") : second), [{ push: replanned }, SETTLE, { push: again, wait: 1 }, { wait: 500 }], { quicker: 20 });
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(planNote(snaps[3].app)).toBeNull();
  expect(outcome(snaps[4].app)).toBe("The second change's outcome.");
  expect(snaps.fetched).toHaveLength(2);
});

test("a push during an ask starts no second ask, and the answer still lands", async () => {
  const { page, replanned, after } = replanning();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const snaps = await live(page, () => held, [{ push: replanned }, SETTLE, { push: replanned }, QUIET, { run: () => release(after) }]);
  expect(snaps.fetched).toHaveLength(1);
  expect(planNote(snaps[4].app)).toBeNull();
  expect(outcome(snaps[5].app)).toBe("The outcome the changed plan states.");
});

test("an answer arriving after the record stopped naming another plan is dropped", async () => {
  const { page, plan, replanned, after } = replanning();
  const settled = structuredClone(replanned);
  settled.journal.push({ id: 15, at: minute(41), kind: "decision.recorded", lane: null, subject: null, summary: `Plan updated: ${plan.updated}; reverted` });
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const snaps = await live(page, () => held, [{ push: replanned }, SETTLE, { push: settled }, { run: () => release(after) }, QUIET]);
  expect(outcome(snaps[4].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[5].app)).toBeNull();
  expect(snaps.fetched).toHaveLength(1);
});

test("once out of date, an answer that is merely older keeps the note", async () => {
  const { page, plan, replanned } = replanning();
  let asked = 0;
  const snaps = await live(page, () => (asked++ === 0 ? new Error("down") : page), [{ push: replanned }, { asked: 1 }, { asked: 2 }], { quicker: 500 });
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(planNote(snaps[3].app)).toBe(OUT_OF_DATE);
  expect(outcome(snaps[3].app)).toBe(plan.outcome.summary);
});

const CLIPPED_CHANGE = { id: "c1", at: minute(30), kind: "changed", subjects: ["PAY-1"], by: "lead", why: "", title: "", clipped: true };
const CLIPPED_WORDS = "The plan was too large to show whole: the text of the 1 oldest change left out.";
const changes = '.plan-fold[data-fold="changes"]';

test("what the renderer says about the plan it clipped comes and goes with that plan", async () => {
  const { document, plan, changed, replanned } = replanning();
  const clipped = render(replanned, undefined, { planBlock: { plan: { ...changed, changes: [CLIPPED_CHANGE] }, clipped: { changes: 1, texts: 0 } } });
  const taken = await live(render(document, plan), clipped, [{ push: replanned }, SETTLE]);
  expect(taken[0].app.querySelector("#plan .plan-clipped")).toBeNull();
  expect(taken[2].app.querySelector("#plan .plan-clipped").textContent).toBe(CLIPPED_WORDS);
  // A plan the page can't draw goes back with what was said about the plan it replaced.
  const held = render(document, undefined, { planBlock: { plan: { ...plan, changes: [CLIPPED_CHANGE] }, clipped: { changes: 1, texts: 0 } } });
  const broken = render(replanned, undefined, { planBlock: { plan: { ...changed, units: [{ ...changed.units[0], proofs: [null] }] } } });
  const kept = await live(held, broken, [{ push: replanned }, SETTLE]);
  expect(kept[2].app.querySelector("#plan .plan-clipped").textContent).toBe(CLIPPED_WORDS);
  expect(planNote(kept[2].app)).toBe(OUT_OF_DATE);
});

test("a plan that fails partway through its own chapter goes back too, with the note, and asks at the slow pace", async () => {
  const { page, plan, changed, replanned } = replanning();
  // It gets past the chapters above and through the page's asking; only the plan chapter's rows fail.
  const broken = render(replanned, undefined, { planBlock: { plan: { ...changed, units: [{ ...changed.units[0], removed: true, proofs: [null] }, ...changed.units.slice(1)] } } });
  const snaps = await live(page, broken, [{ push: replanned }, SETTLE, QUIET]);
  expect(outcome(snaps[2].app)).toBe(plan.outcome.summary);
  expect(planNote(snaps[2].app)).toBe(OUT_OF_DATE);
  expect(planNote(snaps[3].app)).toBe(OUT_OF_DATE);
  expect(snaps.fetched).toHaveLength(1);
});

test("a live page takes a later plan than the record names, and comes to rest on it", async () => {
  const { page, changed, replanned } = replanning();
  const newer = render(replanned, { ...changed, updated: "2026-10-30T14:00:00Z", outcome: { ...changed.outcome, summary: "A later outcome still." } });
  const snaps = await live(page, newer, [{ push: replanned }, SETTLE, { asked: 16 }, { push: replanned }, QUIET]);
  expect(outcome(snaps[2].app)).toBe("A later outcome still.");
  expect(planNote(snaps[3].app)).toBeNull();
  expect(snaps.fetched).toHaveLength(16);
});

test("a live page whose plan couldn't be read when it opened takes the repaired one", async () => {
  const { document, replanned, after } = replanning();
  const snaps = await live(render(document, undefined, { planBlock: { unreadable: "units: must be a list" } }), after, [{ push: replanned }, SETTLE]);
  expect(flags(snaps[0].app)).toEqual(["The plan couldn't be read"]);
  expect(outcome(snaps[2].app)).toBe("The outcome the changed plan states.");
});

test("a static page has no server to ask, and keeps the note", async () => {
  const { plan, page, replanned } = replanning();
  // A store and a fetch but no http address: nothing to fetch from.
  const pushed = await open(page, { store: { read: "pending" }, quicker: 100, strayFetch: true, steps: [{ push: replanned }, QUIET] });
  expect(pushed.fetched).toHaveLength(0);
  expect(planNote(pushed[2].app)).toBe(RELOAD);
  expect(outcome(pushed[2].app)).toBe(plan.outcome.summary);
  // No store at all: a render from a record that already names another plan.
  const [saved] = await open(render(replanned, plan), { store: false });
  expect(planNote(saved.app)).toBe(RELOAD);
  // An address but no stream: a file on a plain web server, whose next answer is the same file.
  const served = await open(render(replanned, plan), { store: false, quicker: 100, rendered: () => new Error("no ask expected"), steps: [QUIET] });
  expect(served.fetched).toHaveLength(0);
  expect(planNote(served[1].app)).toBe(RELOAD);
});

test("open folds survive the refresh", async () => {
  const { page, replanned, after } = replanning();
  const units = '#plan .plan-fold[data-fold="units"]';
  const snaps = await live(page, after, [{ toggle: units }, { push: replanned }, SETTLE]);
  expect(snaps[1].app.querySelector(units).hasAttribute("open")).toBe(true);
  expect(outcome(snaps[3].app)).toBe("The outcome the changed plan states.");
  expect(snaps[3].app.querySelector(units).hasAttribute("open")).toBe(true);
  expect(snaps[3].app.querySelector('#plan .plan-fold[data-fold="changes"]')?.hasAttribute("open") ?? false).toBe(false);
});

test("a reader within the plan stays where they are, and one past it moves with its end, counting what changed above it", async () => {
  const { document, plan, replanned } = replanning();
  const line = finishLine("grown", { criteria: [], proofs: ["merged"] }, { label: "A finish line added above the chapter" });
  const after = render(replanned, { ...plan, updated: "2026-10-30T13:00:00Z", units: [...plan.units, { ...unit("GROWN"), proofs: plan.units[0].proofs }], finish: [...plan.finish, line] });
  const scenario = (y) => ({ scroll: { y }, quicker: 10 });
  const within = await live(render(document, plan), after, [{ push: replanned }, SETTLE], scenario(10));
  expect(within[2].text).toContain("GROWN");
  expect(within.scrollY).toBe(10);
  const past = await live(render(document, plan), after, [{ push: replanned }, SETTLE], scenario(100000));
  expect(past[2].text).toContain("GROWN");
  const moved = planEnd(past[2].app) - planEnd(past[1].app);
  expect(moved).toBeGreaterThan(0);
  expect(past.scrollY).toBe(100000 + moved);
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
  expect(snaps[1].app.querySelector("#detail .d-plan").textContent).toBe("See in the plan");
  expect(snaps[1].app.querySelector("#detail .d-plan-row").className).toBe("d-plan-row");
  expect(snaps[1].app.querySelector("#detail .d-plan-row .pu-standing").textContent).toBe("2 open");
  expect(snaps[1].app.querySelector("#detail .d-plan-next").textContent).toBe("Merged: not merged yet");
  expect(snaps[2].focused).toBe("li");
  expect(snaps[2].app.querySelector('.plan-fold[data-fold="units"]').open).toBe(false);
  snaps = await open(page, { steps: [{ click: '.stop-btn[data-subject="CART-101"]' }, { click: "#detail .d-plan" }] });
  expect(snaps[1].app.querySelector("#detail .d-plan-row").className).toBe("d-plan-row proven");
  expect(snaps[1].app.querySelector("#detail .d-plan-row").textContent).toBe("ProvenSee in the plan");
  // Where the unit stands sits with the stop's title and links, above its run and steps.
  expect([...snaps[1].app.querySelector("#detail").children].findIndex((node) => node.classList.contains("d-plan-row"))).toBe(3);
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
  expect(snaps[1].app.querySelector("#detail .d-plan-row")).toBeNull();
});

test("on a phone each unit is a card with labelled proofs, and both themes colour the chapter from their tokens", async () => {
  const css = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "line-map.css"), "utf8");
  const section = css.slice(css.indexOf("/* The plan:"));
  const phone = section.slice(section.indexOf("@media (max-width: 640px)"));
  expect(phone).toContain(".plan-unit { grid-template-columns: 1fr;");
  expect(phone).toContain(".plan-grid-head { display: none; }");
  expect(section).not.toContain(".pp-kind { display: none");
  // No colour of its own: every one is a theme token that the light and both dark rules define.
  expect(section).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
  const tokens = [...new Set([...section.matchAll(/var\(--([a-z0-9-]+)/g)].map((match) => match[1]))];
  const rule = (selector) => css.slice(css.indexOf(selector), css.indexOf("}", css.indexOf(selector)));
  for (const scope of [":root {", ':root:not([data-theme="light"]) {', ':root[data-theme="dark"] {']) {
    const defined = rule(scope);
    for (const token of tokens.filter((name) => !["display", "text", "ease", "mono"].includes(name))) expect([scope, token, defined.includes(`--${token}:`)]).toEqual([scope, token, true]);
  }
  // Every owed proof names its kind, since one status column carries them all.
  const { document, plan } = recordOnlyDelivery();
  const app = await first(document, plan);
  const row = app.querySelector(".plan-all .plan-unit");
  expect([...row.querySelectorAll(".plan-proof .pp-kind")].map((node) => node.textContent).sort()).toEqual(["Handed off", "Merged", "Review"]);
  const inApp = inAppDelivery();
  expect((await first(inApp.document, inApp.plan)).querySelector(".plan-all .plan-unit").querySelectorAll(".plan-proof")).toHaveLength(4);
});

test("each unit's status says where it stands, puts what is open first, and its name carries its ticket", async () => {
  const cell = (app, subject) => [...app.querySelectorAll("#plan .plan-all .plan-unit")]
    .find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === subject);
  const standing = (app, subject) => cell(app, subject).querySelector(".pu-standing").textContent;
  const order = (app, subject) => [...cell(app, subject).querySelectorAll(".plan-proof .pp-kind")].map((node) => node.textContent);
  const plan = planFor([unit("A"), unit("B"), unit("C", { label: "Card retry" })], { finish: [] });
  const app = await first(record({
    queue: [item("A", "merged"), item("B", "merged", { head: headOf("B", 1) }), item("C", "queued", { pending: true })],
    journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"], [13, "approve", "B"], [14, "candidate.handed", "B"],
      [15, "candidate.merged", "B"], [16, "candidate.handed", "B", { head: headOf("B", 1) }], [17, "approve", "C"], [18, "candidate.handed", "C"]]) }), plan);
  expect(standing(app, "A")).toBe("Proven");
  expect(cell(app, "A").classList.contains("proven")).toBe(true);
  expect(order(app, "A")).toEqual(["Review", "Handed off", "Merged"]);
  expect(standing(app, "B")).toBe("Merged, 1 open");
  expect(order(app, "B")).toEqual(["Review", "Handed off", "Merged"]);
  expect(standing(app, "C")).toBe("2 open");
  expect(order(app, "C")).toEqual(["Handed off", "Merged", "Review"]);
  expect(cell(app, "C").querySelector(".pu-key").textContent).toBe("C");
  expect(cell(app, "A").querySelector(".pu-key")).toBeNull();
  expect(app.querySelector("#plan .plan-grid-head").textContent).toBe("UnitCriteriaStatus");
});

test("a meter names what it counts, a line with nothing to count has no bar, and met with open proofs says both", async () => {
  const { document, plan } = proven(["A", "B"], { finish: [finishLine("landed", { criteria: "all", proofs: ["merged"] }, { label: "Landed" }),
    finishLine("everything", { criteria: "all", proofs: [] }, { label: "Everything" }), finishLine("closed", { criteria: [], proofs: [] }, { label: "Closed out" }),
    finishLine("closed-early", { criteria: [], proofs: [] }, { label: "Not closed" })], queue: [item("A", "merged"), item("B", "queued")] });
  document.state.finish = [{ id: "landed", text: "Landed", met: true }, { id: "everything", text: "Everything", met: false },
    { id: "closed", text: "Closed out", met: true }, { id: "closed-early", text: "Not closed", met: false }];
  const app = await first(document, plan);
  const meters = [...app.querySelectorAll("#left .plan-meter")];
  expect(meters.map((meter) => [meter.className, meter.textContent])).toEqual([
    ["plan-meter open", "LandedMet, proofs openMerged: 1 of 2 criteria"], ["plan-meter", "EverythingNot metProven: 1 of 2 criteria"],
    ["plan-meter met", "Closed outMetRecorded by the lead, not counted from proofs"], ["plan-meter", "Not closedNot metRecorded by the lead, not counted from proofs"]]);
  expect(meters.map((meter) => meter.querySelector(".pm-bar") !== null)).toEqual([true, true, false, false]);
});

test("a closed delivery reads as closed, and work resuming after the close reopens it", async () => {
  const close = [40, "delivery.closed", "A"];
  const closing = (steps, queue = [item("A", "merged")]) => {
    const document = record({ queue, journal: journal(steps) });
    document.state.lanes = [{ id: "lane-1", title: "Lane one", state: "blocked", waitingOn: "lead", focus: "A merged", since: minute(0) }];
    document.state.meta = { ...document.state.meta, updatedAt: minute(-600) };
    return document;
  };
  const plan = planFor([unit("A")], { finish: [], approvals: [{ text: "Sign off", who: "owner", state: "waiting" }] });
  const landed = [[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"]];

  const app = await first(closing([...landed, close]), plan);
  expect(app.querySelector("#now .title").textContent).toBe("Delivered.");
  expect(app.querySelector("#now .since").className).toBe("since closed");
  expect(app.querySelector("#now .since").textContent).toMatch(/^Closed /);
  expect(app.querySelector("#now .attention")).toBeNull();
  expect(app.querySelector("#left .left-number")).toBeNull();
  expect(app.querySelector("#left .title").textContent).toBe("Nothing left to land.");
  expect(app.querySelector("#left .say").textContent).toBe("1 of 1 pieces have landed.");
  expect(app.querySelector("#who .title").textContent).toBe("This delivery is closed; no lane is working.");
  expect(app.querySelector("#who .st").textContent).toBe("Finished");
  expect(app.querySelector("#who .said")).toBeNull();
  expect(app.querySelector("#plan .plan-calm")).toBeNull();
  expect(app.querySelector('#plan .plan-fold[data-fold="approvals"] summary').textContent).toBe("Approvals1");
  expect(app.querySelector(".plan-approvals li").className).toBe("lapsed");
  expect(app.querySelector(".plan-approvals li span").textContent).toBe("Not recorded");

  const short = await first(closing([[10, "approve", "A"], [11, "candidate.handed", "A"], close], [item("A", "queued")]), plan);
  expect(short.querySelector("#now .title").textContent).toBe("Closed with 0 of 1 pieces landed.");
  expect(short.querySelector("#left .left-number small").textContent).toBe("piece not landed");
  expect(short.querySelector("#now .checking")).toBeNull();

  const resumed = await first(closing([...landed, close, [50, "candidate.handed", "A"]], [item("A", "queued")]), plan);
  expect(resumed.querySelector("#now .title").textContent).not.toMatch(/Delivered|Closed/);
  expect(resumed.querySelector("#left .left-number").textContent).toBe("1piece to go");
  expect(resumed.querySelector("#who .st").textContent).toContain("waiting on lead");
  expect(resumed.querySelector("#plan .plan-calm, #plan .plan-needs .ah")).not.toBeNull();
  expect(resumed.querySelector('#plan .plan-fold[data-fold="approvals"] summary').textContent).toBe("Approvals ahead1");
});

test("what a closed delivery claims holds in every state the record can leave it", async () => {
  const titleOf = (app) => app.querySelector("#now .title").textContent;
  const closedAt = 40;
  const closing = (steps, queue, more = {}) => {
    const document = record({ queue, journal: journal(steps) });
    Object.assign(document.state, more);
    return document;
  };
  const landed = [[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"]];
  const close = [closedAt, "delivery.closed", "A"];

  // Plan units that never reached the line haven't landed, and nothing on the line claims nothing.
  const missing = await first(closing([...landed, close], [item("A", "merged")]), planFor([unit("A"), unit("B")], { finish: [] }));
  expect(titleOf(missing)).toBe("Closed with 1 of 2 pieces landed.");
  expect(missing.querySelector("#left .left-number").textContent).toBe("1piece not landed");
  expect(missing.querySelector("#left .say").textContent).toBe("1 of 2 pieces have landed.");
  const empty = await first(closing([close], []));
  expect(titleOf(empty)).toBe("Closed with nothing on the line.");
  expect(empty.querySelector("#left .title").textContent).toBe("Nothing was on the line.");
  expect(empty.querySelector("#left .left-number")).toBeNull();
  expect(empty.querySelector("#left .say")).toBeNull();

  // Nothing reads as still happening: a stop, its lane's count, a hold, a lease or the next finish line.
  const short = closing([[10, "candidate.handed", "B"], [11, "check.started", "B"], close], [item("B", "checking")], {
    leases: [{ id: "app", holder: "lane-1", since: minute(0) }], finish: [{ id: "next", text: "First line", met: false }, { id: "then", text: "Second line", met: false }] });
  short.state.meta = { ...short.state.meta, hold: { reason: "paused" } };
  const [shown, stop] = await open(render(short), { steps: [{ click: '.stop-btn[data-subject="B"]' }] });
  expect(shown.app.querySelector("#now .attention")).toBeNull();
  expect(shown.app.querySelector("#who .leases")).toBeNull();
  expect(shown.app.querySelector("#left .left-groups")).toBeNull();
  expect(shown.app.querySelector("#left .after").textContent).toBe("Not metFirst lineNot metSecond line");
  expect(shown.app.querySelector("#left .after .on")).toBeNull();
  expect(stop.app.querySelector("#detail .d-stage").textContent).toBe("Not landed");

  // Only new work reopens it, and each kind of new work does.
  for (const kind of ["lane.assigned", "candidate.handed", "check.started"]) {
    expect([kind, titleOf(await first(closing([...landed, close, [50, kind, "A"]], [item("A", "merged")])))]).toEqual([kind, "Every piece has landed."]);
  }
  for (const kind of ["review.round", "check.finished", "decision.recorded", "candidate.merged"]) {
    expect([kind, titleOf(await first(closing([...landed, close, [50, kind, "A", kind === "review.round" ? { review: { round: 2 } } : {}]], [item("A", "merged")])))]).toEqual([kind, "Delivered."]);
  }

  // Losing the page's connection still shows, and a close time the clock can't call past stands alone.
  const closedRecord = closing([...landed, close], [item("A", "merged")]);
  const [connected, lost] = await open(render(closedRecord), { store: { read: "pending" }, steps: [{ push: closedRecord }, { status: false }] }).then((snaps) => [snaps[1], snaps[2]]);
  expect(connected.app.querySelector("#now .since").className).toBe("since closed");
  expect(lost.app.querySelector("#now .since").textContent).toContain("Reconnecting. Showing the last update.");
  const future = closing([...landed], [item("A", "merged")]);
  future.journal.push({ id: 90, at: "2999-01-01T00:00:00Z", kind: "delivery.closed", lane: null, subject: "A", summary: "closed" });
  expect((await first(future)).querySelector("#now .since").textContent).toMatch(/^Closed( · base \w+)?$/);
  const timeless = closing([...landed], [item("A", "merged")]);
  timeless.journal.push({ id: 90, at: null, kind: "delivery.closed", lane: null, subject: "A", summary: "closed" });
  expect((await first(timeless)).querySelector("#now .since").textContent).toMatch(/^Closed( · base \w+)?$/);
});

test("a closed delivery's bar, dots, stops, legend and attention all agree with its count", async () => {
  const close = [40, "delivery.closed", "A"];
  const landed = [[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"]];
  const document = record({ queue: [item("A", "merged"), item("B", "checking"), item("C", "queued", { pending: true, leadInputs: ["key"] }), item("D", "stopped")],
    journal: journal([...landed, close]) });
  document.state.planned = [{ subject: "E", stage: "building" }];
  document.state.finish = [];
  document.estimates = { C: { position: 1, expectedStart: minute(99) } };
  document.state.meta = { ...document.state.meta, sources: { tracker: { status: "stale", reason: "gone" } }, hold: { reason: "paused" } };
  document.journal.push({ id: 90, at: minute(30), kind: "base.broken", lane: null, subject: null, summary: "Main is red" }, { ...document.journal.find((entry) => entry.kind === "delivery.closed"), id: 91, at: minute(41) });
  const plan = planFor(["A", "B", "C", "D", "E", "F"].map((subject) => unit(subject)), { finish: [] });
  const [shown, stop] = await open(render(document, plan), { steps: [{ click: '.stop-btn[data-subject="C"]' }] });
  const app = shown.app;
  expect(app.querySelector("#now .title").textContent).toBe("Closed with 1 of 6 pieces landed.");
  expect(Math.round(parseFloat(app.querySelector("#now .journey .done").style.width))).toBe(17);
  expect([...app.querySelectorAll("#left .dots i")].map((dot) => dot.className).filter((name) => name === "open")).toHaveLength(5);
  expect(app.querySelector("#left .left-number").textContent).toBe("5pieces not landed");
  for (const subject of ["B", "C", "D", "E"]) {
    const button = app.querySelector(`.stop-btn[data-subject="${subject}"]`);
    expect([subject, button.className.trim(), button.getAttribute("aria-label").endsWith(", Not landed")]).toEqual([subject, "stop-btn unlanded", true]);
  }
  expect(app.querySelector('.stop-btn[data-subject="A"]').className).toContain("merged");
  expect(app.querySelector(".stop-btn.now")).toBeNull();
  expect([...app.querySelectorAll(".line-legend span")].map((entry) => entry.textContent)).toEqual(["Landed", "Not landed", "Both landed"]);
  expect(stop.app.querySelector("#detail").textContent).not.toMatch(/Needs the lead first|Position 1 in line/);
  // What was true at the close stays, under words that don't ask for anything; a silent source and a hold don't.
  const attention = app.querySelector("#now .attention");
  expect(attention.querySelector(".ah").textContent).toBe("Left open at close");
  expect([...attention.querySelectorAll(".a1")].map((row) => row.textContent).sort()).toEqual(["The main line is broken", "Work D stopped"]);
  // The count keeps its number when the same closed record is pushed again.
  const again = await open(render(document, plan), { store: { read: "pending" }, steps: [{ push: document }, { push: document }] });
  expect(again[2].app.querySelector("#left .left-number").textContent).toBe("5pieces not landed");

  // A plan that can't be drawn on a closed delivery doesn't ask for anything either.
  const [missing] = await open(render(record({ queue: [item("A", "merged")], journal: journal([...landed, close]) }), undefined, { planBlock: { absent: "docs/plans/pay.md" } }));
  expect(missing.app.querySelector("#plan .plan-needs .ah").textContent).toBe("Left open at close");
});

test("standing and stop wording hold without a merged proof, with a waiver beside an open proof, and for a removed unit", async () => {
  const landed = [[10, "approve", "A"], [11, "candidate.handed", "A"]];
  const noMerged = planFor([unit("A", { proofs: ["review", "handed"] }), unit("B", { proofs: ["review"] })], { kinds: RECORD_KINDS.filter((kind) => kind.id !== "merged"), finish: [] });
  const without = await first(record({ queue: [item("A", "queued")], journal: journal(landed) }), noMerged);
  const standing = (app, subject) => [...app.querySelectorAll("#plan .plan-all .plan-unit")]
    .find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === subject).querySelector(".pu-standing").textContent;
  expect(standing(without, "B")).toBe("1 open");

  const waived = proven(["A"], { kinds: [...RECORD_KINDS, APP_KIND], queue: [item("A", "queued")], extraSteps: [],
    evidence: { A: { app: { state: "waived", head: headOf("A"), reason: "No device." } } } });
  expect(standing(await first(waived.document, waived.plan), "A")).toBe("1 open");

  const removedPlan = planFor([unit("A", { removed: true })], { finish: [] });
  const [, removedStop] = await open(render(record({ queue: [item("A", "merged")], journal: journal([...landed, [12, "candidate.merged", "A"]]) }), removedPlan), { steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  expect(removedStop.app.querySelector("#detail .d-plan-row .pu-standing").textContent).toBe("Removed from the plan");
  expect(removedStop.app.querySelector("#detail .d-plan-row").className).toBe("d-plan-row removed");

  const settled = proven(["A", "B"], { kinds: [...RECORD_KINDS, APP_KIND], finish: [finishLine("checked", { criteria: "all", proofs: ["app"] }, { label: "Checked" })],
    evidence: { A: { app: { state: "waived", head: headOf("A"), reason: "No device." } }, B: { app: { state: "waived", head: headOf("B"), reason: "No device." } } } });
  settled.document.state.finish = [{ id: "checked", text: "Checked", met: false }];
  expect((await first(settled.document, settled.plan)).querySelector("#left .plan-meter .pm-state").textContent).toBe("Not marked met");
});

test("a waived proof is settled on a meter, a removed unit owes nothing, and a full count not marked met says so", async () => {
  const { document, plan } = proven(["A", "B"], { kinds: [...RECORD_KINDS, APP_KIND], finish: [finishLine("checked", { criteria: "all", proofs: ["app"] }, { label: "Checked" })],
    evidence: { A: { app: { state: "waived", head: headOf("A"), reason: "no screen" } }, B: { app: { state: "done", head: headOf("B") } } } });
  document.state.finish = [{ id: "checked", text: "Checked", met: true }];
  const app = await first(document, plan);
  expect(app.querySelector("#left .plan-meter .pm-state").textContent).toBe("Met");

  const removedPlan = planFor([unit("A"), unit("B", { removed: true })], { finish: [] });
  const removed = await first(record({ queue: [item("A", "merged")], journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"]]) }), removedPlan);
  const row = [...removed.querySelectorAll("#plan .plan-all .plan-unit")].find((entry) => entry.querySelector("[data-ticket]").getAttribute("data-ticket") === "B");
  expect(row.querySelector(".plan-proof")).toBeNull();
  expect(row.querySelector(".pu-standing")).toBeNull();
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
  expect(app.querySelector(".plan-all .pu-criteria li").textContent).toBe(`${hostile} x.1`);
  // The unit is found by its subject through the page's own map, so the stop's link reaches it.
  expect(snaps[3].focused).toBe("li");
  // A ref the renderer linked, a change request on the record, opens as that link does on the line.
  const linked = record({ queue: [item("PAY-9", "queued", { changeRequest: "#77" })], journal: journal([[11, "candidate.handed", "PAY-9"]]) });
  const linkedPage = pageWithRuns(linked, {}, { config: { ...CONFIG, forges: [{ name: "Forge", repository: "acme/shop", pullUrl: "https://forge.example/{repository}/pull/{number}", commitUrl: "https://forge.example/{repository}/commit/{sha}" }] },
    plan: planFor([unit("PAY-9")], { changes: [{ id: "c1", at: minute(30), kind: "changed", subjects: ["PAY-9"], by: "lead", why: "Moved.", ref: "#77" }] }) });
  const [shown] = await open(linkedPage);
  expect(shown.app.querySelector(".plan-change .pc-why a").getAttribute("href")).toBe("https://forge.example/acme/shop/pull/77");
});
