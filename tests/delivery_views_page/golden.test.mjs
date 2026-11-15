// The line map equals the golden outputs captured from its source design, case by case. A case with a plan.json renders
// with it in the state directory and also pins the plan chapter's folds; a case without one embeds no plan at all.
import assert from "node:assert/strict";
import { readdirSync, existsSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { CORPUS, ROOT, block, openPage, pageWithRuns, readJson, renderPage } from "./support.mjs";

process.env.TZ = "UTC";

const CASES = readdirSync(CORPUS).filter((name) => existsSync(path.join(CORPUS, name, "golden.json"))).sort();
const CONFIG = readJson(path.join(CORPUS, "linked-config.json"));

test("the corpus holds every captured case", () => {
  assert.deepEqual(CASES, ["delivery-records-epic", "parked-no-runs", "parked-round-report-unreadable", "parked-round-reports",
    "parked-run-view", "plan-in-app", "plan-record-only", "plan-review", "round-card-record"]);
});

for (const name of CASES) {
  test(`${name}: the embedded record and runs, the page text and every detail panel equal the golden`, async () => {
    const folder = path.join(CORPUS, name);
    const golden = readJson(path.join(folder, "golden.json"));
    const document = readJson(path.join(folder, "document.json"));
    const planFile = path.join(folder, "plan.json");
    const plan = existsSync(planFile) ? readJson(planFile) : null;
    const page = renderPage(document, { config: CONFIG, runsCommands: readJson(path.join(folder, "runs-commands.json")), plan });
    assert.deepEqual(block(page, "delivery-state"), golden.blocks["delivery-state"]);
    assert.deepEqual(block(page, "delivery-runs"), golden.blocks["delivery-runs"]);
    assert.deepEqual(block(page, "delivery-plan"), golden.blocks["delivery-plan"] ?? null);
    const [first] = await openPage(page, { now: golden.now });
    assert.equal(first.text, golden.initialText);
    for (const [name, expected] of Object.entries(golden.folds ?? {})) {
      const fold = `.plan-fold[data-fold="${name}"]`;
      const [, opened] = await openPage(page, { now: golden.now, steps: [{ toggle: fold }] });
      assert.equal(opened.app.querySelector(fold).textContent, expected, name);
    }
    for (const [subject, expected] of Object.entries(golden.details)) {
      const stop = `.stop-btn[data-subject="${subject}"]`;
      const [, shown] = await openPage(page, { now: golden.now, steps: [{ click: stop }] });
      assert.equal(shown.app.querySelector("#detail").textContent, expected.detail, subject);
      const picks = shown.app.querySelectorAll("#detail .run-pick").length;
      assert.equal(picks, expected.rounds ? expected.rounds.length : 0, subject);
      if (!picks) continue;
      const snaps = await openPage(page, { now: golden.now,
        steps: [{ click: stop }, ...Array.from({ length: picks }, (_, index) => ({ click: "#detail .run-pick", index }))] });
      assert.deepEqual(snaps.slice(2).map((snap) => snap.app.querySelector("#detail .run-card")?.textContent ?? ""), expected.rounds, subject);
    }
  });
}

test("the link block holds the configured tracker's and forge's addresses for the record's ids", () => {
  const folder = path.join(CORPUS, "parked-round-reports");
  const page = renderPage(readJson(path.join(folder, "document.json")),
    { config: CONFIG, runsCommands: readJson(path.join(folder, "runs-commands.json")) });
  assert.deepEqual(block(page, "delivery-links"), readJson(path.join(CORPUS, "linked-links.json")));
});

// A review whose round 1 approved with one P2 and three P3 findings, all filed as deferred, which round 2 fixed. Its
// cards are drawn from what the renderer projects of the record's round files, never from a hand-written card.
const DEFERRED = path.join(ROOT, "tests", "fixtures", "delivery-views", "round-card-deferred");
const VIEWS = { spec: "delivery-views-config/1" };

function deferredRecord(change = () => {}) {
  const document = readJson(path.join(DEFERRED, "document.json"));
  change(document.reviewEvidence["WORK-1"].outcomes, document);
  return document;
}
async function deferredCards(document) {
  const picks = document.reviewEvidence["WORK-1"].outcomes.length;
  const snaps = await openPage(pageWithRuns(document, {}, { config: VIEWS }),
    { steps: Array.from({ length: picks }, (_, index) => ({ click: ".run-pick", index })) });
  return snaps.slice(1).map((snap) => snap.app.querySelector("#detail .run-card"));
}
const pill = (card) => card.querySelector(".run-card-outcome").textContent;
const marks = (card) => Array.from(card.querySelectorAll(".run-filed"), (mark) => mark.textContent);

test("a round that approved with deferred findings says so at its head and on each row, as the golden holds", async () => {
  const golden = readJson(path.join(DEFERRED, "golden.json"));
  const cards = await deferredCards(deferredRecord());
  assert.deepEqual(cards.map((card) => card.textContent), golden.rounds);
  const [first] = cards;
  assert.equal(pill(first), "Approved with 4 deferrable notes");
  assert.deepEqual(marks(first), ["Deferred · minor", "Deferred · minor", "Deferred · moderate", "Deferred · minor"]);
  assert.equal(first.querySelector(".run-settled").textContent, "All 4 fixed in bbbbbbb · confirmed round 2");
});

test("a deferred finding a later round leaves unfixed is still counted and marked", async () => {
  const [first] = await deferredCards(deferredRecord((outcomes) => {
    outcomes[1].entries[1].result.resolvedFindings.pop();
  }));
  assert.equal(pill(first), "Approved with 4 deferrable notes");
  assert.deepEqual(marks(first), ["Deferred · minor", "Deferred · minor", "Deferred · moderate", "Deferred · minor"]);
  assert.equal(first.querySelector(".run-settled").textContent, "3 of 4 fixed in bbbbbbb · confirmed round 2");
});

test("a round that approved with one deferred finding says it in the singular", async () => {
  const [first] = await deferredCards(deferredRecord((outcomes) => {
    outcomes[0].entries[0].result.findings.pop();
    outcomes[0].entries[1].result.findings = [];
    outcomes[1].entries[0].result.resolvedFindings.pop();
    outcomes[1].entries[1].result.resolvedFindings = [];
  }));
  assert.equal(pill(first), "Approved with 1 deferrable note");
  assert.deepEqual(marks(first), ["Deferred · moderate"]);
});

test("a round that raised nothing still reads Approved, and a round with notes open keeps its words", async () => {
  const [, second] = await deferredCards(deferredRecord());
  assert.equal(pill(second), "Approved");
  assert.deepEqual(marks(second), []);
  const [open] = await deferredCards(deferredRecord((outcomes, document) => {
    outcomes.pop();
    outcomes[0].outcome = "unresolved";
    const kept = outcomes[0].entries[0].result.findings[1];
    kept.state = "open";
    delete kept.deferredIssue;
    document.reviewEvidence["WORK-1"].candidateRef = document.state.queue[0].head = "a".repeat(40);
  }));
  assert.equal(pill(open), "Approved, notes open");
  assert.deepEqual(marks(open), ["Deferred · minor", "Deferred · minor", "Deferred · moderate"]);
});
