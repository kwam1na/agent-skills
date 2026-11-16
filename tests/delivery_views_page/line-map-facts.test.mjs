// The page's readers of a journal entry read its facts, never its words. Each finds what it looks for in entries that
// carry facts and no sentence, and finds the same when every prose field of those entries says something else.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, minute, planFor, record, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const PROSE = ["text", "why", "detail", "note", "notes", "title"];

/** The same entries, every prose field saying something else. */
function reworded(journal) {
  return journal.map((entry) => ({ ...entry, data: Object.fromEntries(Object.entries(entry.data).map(([key, value]) =>
    [key, PROSE.includes(key) ? (Array.isArray(value) ? value.map(() => "Other words.") : "Other words.") : value])) }));
}
function entry(id, at, kind, data, subject = null, links = {}) {
  return { id, at: minute(at), kind, lane: subject ? "lane-1" : null, subject, data, ...links };
}
async function first(document, plan) {
  const [shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG, ...(plan ? { plan } : {}) }), { now: NOW, store: false });
  return shown.app;
}

test("the checking sheet finds a batch by the members its start names", async () => {
  const journal = [entry(1, 10, "check.started", { batch: ["A", "B"] }, "A"), entry(2, 10, "check.started", { batch: ["A", "B"] }, "B")];
  for (const entries of [journal, reworded(journal)]) {
    const document = record({ queue: [item("A", "checking"), item("B", "checking")], journal: entries });
    document.state.meta.checking = { now: { subject: "A", title: "Work A", startedAt: minute(10), steps: [] }, next: [] };
    expect((await first(document)).querySelector(".checking .ck-h-words").textContent).toBe("Being checked together · 2 pieces");
  }
  // A start that names no batch is a piece checked alone.
  const alone = record({ queue: [item("A", "checking"), item("B", "checking")], journal: [entry(1, 10, "check.started", {}, "A")] });
  alone.state.meta.checking = { now: { subject: "A", title: "Work A", startedAt: minute(10), steps: [] }, next: [] };
  expect((await first(alone)).querySelector(".checking .ck-h-words").textContent).toBe("Checking · A");
});

test("the plan chapter finds a plan update by its kind and the instant it names", async () => {
  const plan = planFor([unit("A")], { finish: [], updated: "2026-10-30T09:00:00Z" });
  for (const [updated, notes] of [["2026-10-30T13:00:00Z", 1], [plan.updated, 0]]) {
    const journal = [entry(1, 10, "plan.updated", { updated, changed: ["units"] })];
    for (const entries of [journal, reworded(journal)]) {
      const app = await first(record({ queue: [item("A", "queued")], journal: entries }), plan);
      expect([updated, app.querySelectorAll("#plan .plan-reload").length]).toEqual([updated, notes]);
    }
  }
  // A decision whose words say a plan was updated is no plan write.
  const said = [entry(1, 10, "decision.recorded", { text: "Plan updated: 2026-10-30T13:00:00Z", why: "It changed." })];
  expect((await first(record({ queue: [item("A", "queued")], journal: said }), plan)).querySelectorAll("#plan .plan-reload").length).toBe(0);
});

test("a stop's notes sit under the check whose failure they name among their causes", async () => {
  const journal = [
    entry(1, 10, "check.started", {}, "A"),
    entry(2, 12, "check.failed", { check: "suite", timedOut: false, failing: ["suite.Case.test_it"], more: 0, log: "logs/suite.log" }, "A", { because: [1] }),
    entry(3, 13, "decision.recorded", { text: "The suite fails on the receipt page", why: "A fixture lost its total." }, "A", { because: [2] }),
    // Its words name the suite, but nothing ties it to the suite's failure: it is a note about the piece.
    entry(4, 14, "decision.recorded", { text: "The suite and lint both need a look", why: "Later." }, "A"),
  ];
  for (const entries of [journal, reworded(journal)]) {
    const document = record({ queue: [item("A", "checking")], journal: entries });
    document.state.meta.checking = { now: { subject: "A", title: "Work A", startedAt: minute(10),
      steps: [{ label: "suite", state: "failed" }, { label: "lint", state: "failed" }] }, next: [] };
    const app = await first(document);
    const under = (check) => app.querySelectorAll(`.ck-fails li[data-check="${check}"] .ck-note`).length;
    expect([under("suite"), under("lint"), app.querySelectorAll(".ck-more .ck-note").length]).toEqual([1, 0, 1]);
  }
});

test("the plan chapter reads a review round's verdicts from its facts", async () => {
  const plan = planFor([unit("A")], { finish: [], updated: "2026-10-30T09:00:00Z" });
  const verdicts = { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "approve" };
  const journal = [
    entry(1, 10, "review.round", { round: 1, startedAt: minute(5), finishedAt: minute(10), verdicts, findings: { P0: 0, P1: 0, P2: 0, P3: 0 } }, "A"),
    entry(2, 11, "candidate.handed", { head: item("A", "queued").head, changeRequest: "https://example.test/pull/1", rounds: 1, checks: [] }, "A"),
  ];
  for (const entries of [journal, reworded(journal)]) {
    const app = await first(record({ queue: [item("A", "queued")], journal: entries }), plan);
    expect([...app.querySelectorAll("#plan .plan-all .plan-proof")].map((proof) => proof.textContent)).toEqual(["It still has to merge.", "Done: review, handed off"]);
  }
  const asked = structuredClone(journal);
  asked[0].data.verdicts["lens.adversarial-testing"] = "changes";
  const app = await first(record({ queue: [item("A", "queued")], journal: asked }), plan);
  expect([...app.querySelectorAll("#plan .plan-all .plan-proof")].map((proof) => proof.textContent)[0]).toBe("Its review isn't approved yet.");
});
