// The page's readers of a journal entry read its facts, never its words. Each finds what it looks for in entries that
// carry facts and no sentence, and finds the same when every prose field of those entries says something else.
process.env.TZ = "UTC";

import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, told } from "./support.mjs";
import { finishLine, item, minute, planFor, record, unit } from "./plan-fixtures.mjs";

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

/** What the story says of one entry, as one line: its own beat's line, or its head when the line is empty. */
function storyLine(document, id) {
  const beat = told(document).story.beats.find((each) => each.form !== "thread" && each.altitudes.includes("everything") && each.entries.includes(id));
  return beat.words.line || beat.words.head;
}

test("what a closed delivery left open and the base's flag take a stop's and a break's words from the story", async () => {
  const plan = planFor([unit("A")], { finish: [], updated: "2026-10-30T09:00:00Z" });
  const journal = [
    entry(1, 10, "candidate.stopped", { reason: "check_failed", detail: "Its review is for an older head.", stage: "checking" }, "A"),
    entry(2, 12, "base.broken", { source: "fullHealth", detail: "suite.Case.test_it failed.", head: "a".repeat(40), after: "A" }),
  ];
  for (const entries of [journal, reworded(journal)]) {
    const document = record({ queue: [item("A", "stopped")], journal: entries });
    const app = await first(document, plan);
    expect([...app.querySelectorAll("#plan .plan-needs li")].find((row) => row.querySelector(".a1").textContent === "The base is broken")
      .querySelector(".a2").textContent).toBe(storyLine(document, 2));
    // Closed, the stop and the break it left open are said in the same story words, by the renderer from the record.
    const closed = record({ queue: [item("A", "stopped")], journal: [...entries, entry(3, 14, "delivery.closed", {})] });
    const left = await first(closed, plan);
    expect([...left.querySelectorAll("#now .left-open li .a2")].map((row) => row.textContent))
      .toEqual([storyLine(closed, 1), storyLine(closed, 2)]);
  }
});

test("the merge check's steps say what the story says of entries that carry facts and no sentence", async () => {
  const journal = [
    entry(1, 10, "candidate.handed", { head: item("A", "queued").head, changeRequest: "https://example.test/pull/1", rounds: 2, checks: ["lint"] }, "A"),
    entry(2, 11, "check.started", {}, "A"),
    entry(3, 12, "check.failed", { check: "suite", timedOut: false, failing: ["suite.Case.test_it"], more: 0, log: "logs/suite.log" }, "A", { because: [2] }),
  ];
  const document = record({ queue: [item("A", "queued")], journal });
  const [, card] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false, steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  const beats = told(document).story.beats.filter((beat) => beat.form === "entry");
  const said = (id) => { const { words } = beats.find((beat) => beat.entries.includes(id)); return words.head + (words.line ? ": " + words.line : ""); };
  expect([...card.app.querySelectorAll("#detail .mh li span")].map((step) => step.textContent)).toEqual([said(1), said(2), said(3)]);
});

test("a finish line met is told on the page by the plan's label, not the record's", async () => {
  const plan = planFor([unit("A")], { finish: [finishLine("stories", { criteria: [], proofs: ["merged"] }, { label: "Every story lands" })], updated: "2026-10-30T09:00:00Z" });
  const document = record({ queue: [item("A", "merged")], finish: [{ id: "stories", label: "The record's own words", text: "The record's own words", met: true }],
    journal: [entry(1, 10, "finish.met", { finish: "stories" })] });
  const app = await first(document, plan);
  const row = [...app.querySelectorAll("#happened .beat")].find((beat) => beat.querySelector(".beat-said").textContent === "A finish line was met");
  expect(row.querySelector(".beat-line").textContent).toBe("Every story lands");
});
