// The page's readers of a journal entry read its facts, never its words. Each finds what it looks for in entries that
// carry facts and no sentence, and finds the same when every prose field of those entries says something else.
process.env.TZ = "UTC";

import { test } from "node:test";

import { readFileSync } from "node:fs";
import path from "node:path";

import { CORPUS, REPLAYED_RUNS, expect, openPage, pageWithRuns, readJson, renderPage, told } from "./support.mjs";
import { finishLine, item, journal, minute, planFor, record, recordOnlyDelivery, unit } from "./plan-fixtures.mjs";

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

test("the attention rows and the base's flag take a stop's and a break's words from the story", async () => {
  const plan = planFor([unit("A")], { finish: [], updated: "2026-10-30T09:00:00Z" });
  const journal = [
    entry(1, 10, "candidate.stopped", { reason: "check_failed", detail: "Its review is for an older head.", stage: "checking" }, "A"),
    entry(2, 12, "base.broken", { source: "fullHealth", detail: "suite.Case.test_it failed.", head: "a".repeat(40), after: "A" }),
  ];
  for (const entries of [journal, reworded(journal)]) {
    const document = record({ queue: [item("A", "stopped")], journal: entries });
    const app = await first(document, plan);
    const why = (title) => [...app.querySelectorAll("#now .attention li")].find((row) => row.querySelector(".a1").textContent === title).querySelector(".a2").textContent;
    expect([why("Work A stopped"), why("The main line is broken")]).toEqual([storyLine(document, 1), storyLine(document, 2)]);
    expect([...app.querySelectorAll("#plan .plan-needs li")].find((row) => row.querySelector(".a1").textContent === "The base is broken")
      .querySelector(".a2").textContent).toBe(storyLine(document, 2));
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

// The facts row under the line: how many stories have landed, what landed last, and the record's state, counted from
// the record and never from the tracker.
const FORGE = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  forges: [{ name: "GitHub", repository: "acme/shop", pullUrl: "https://github.com/{repository}/pull/{number}",
    commitUrl: "https://github.com/{repository}/commit/{sha}" }] };
const planned = (subject, stage) => ({ subject, title: `Work ${subject}`, lane: "lane-1", stage });
/** Six stories: two landed, two in the queue, two being built; the last landed carries its pull request. */
function underway() {
  return record({
    queue: [item("A", "merged"), item("B", "merged", { changeRequest: "https://github.com/acme/shop/pull/386" }), item("C", "queued"),
      item("D", "checking")],
    planned: [planned("E", "building"), planned("F", "review")],
    journal: journal([[10, "candidate.merged", "A"], [12, "candidate.merged", "B"]]),
  });
}
async function facts(document, plan) {
  const [shown] = await openPage(pageWithRuns(document, {}, { config: FORGE, ...(plan ? { plan } : {}) }), { now: NOW, store: false });
  return shown.app;
}
const track = (app) => [...app.querySelectorAll("#now .stories .fact-track i")].map((part) => [part.className, Math.round(parseFloat(part.style.width))]);

test("the facts row opens with the stories landed out of the total, a track in landed's tones and a count of the queue and the build", async () => {
  const app = await facts(underway());
  // Three facts and no fourth: Stories, Last landed and Live.
  expect([...app.querySelectorAll("#now .facts > .fact")].map((fact) => fact.querySelector(".fact-label").textContent)).toEqual(
    ["Stories", "Last landed", app.querySelector("#now .since .fact-label").textContent]);
  expect(app.querySelector("#now .facts > .fact.since")).toBe(app.querySelectorAll("#now .facts > .fact")[2]);
  const stories = app.querySelector("#now .facts > .fact.stories");
  expect(stories.querySelector(".fact-value .fact-count").firstChild.textContent).toBe("2");
  expect(stories.querySelector(".fact-value .fact-of").textContent).toBe(" of 6 landed");
  expect(stories.querySelector(".fact-value").textContent).toBe("2 of 6 landed");
  expect(track(app)).toEqual([["is-landed", 33], ["is-queued", 33], ["is-building", 33]]);
  expect(stories.querySelector(".fact-track").getAttribute("aria-hidden")).toBe("true");
  expect(stories.querySelector(".fact-said").textContent).toBe("2 in the queue · 2 being built");
});

test("a delivery with nothing landed shows none of its total, and a closed one its final count", async () => {
  const none = await facts(record({ queue: [item("A", "queued")], planned: [planned("B", "building"), planned("C", "building")] }));
  expect(none.querySelector("#now .stories .fact-value").textContent).toBe("0 of 3 landed");
  expect(track(none)).toEqual([["is-queued", 33], ["is-building", 67]]);
  expect(none.querySelector("#now .stories .fact-said").textContent).toBe("1 in the queue · 2 being built");
  // The closed epic landed all nine: its final count, with nothing left in the queue or the build to count.
  const folder = path.join(CORPUS, "delivery-records-epic");
  const page = renderPage(readJson(path.join(folder, "document.json")), { config: readJson(path.join(CORPUS, "linked-config.json")),
    runsCommands: readJson(path.join(folder, "runs-commands.json")) });
  const [closed] = await openPage(page, { now: NOW, store: false });
  expect(closed.app.querySelector("#now .stories .fact-value").textContent).toBe("9 of 9 landed");
  expect(track(closed.app)).toEqual([["is-landed", 100]]);
  expect(closed.app.querySelector("#now .stories .fact-said")).toBeNull();
});

test("last landed names its ticket and, when the forge knows it, its pull request, each a quiet reference", async () => {
  const app = await facts(underway());
  const last = app.querySelectorAll("#now .facts > .fact")[1];
  expect(last.querySelector(".fact-value").textContent).toBe("Work B");
  const refs = [...last.querySelectorAll(".fact-detail .fact-ref")];
  expect(refs.map((ref) => ref.textContent)).toEqual(["B", "#386"]);
  expect(refs[1].getAttribute("href")).toBe("https://github.com/acme/shop/pull/386");
  expect(last.querySelector(".fact-detail").textContent).toMatch(/^B · #386 · /);
  // A landing whose pull request the forge doesn't know says its ticket and when, and no number.
  const unknown = underway();
  delete unknown.state.queue[1].changeRequest;
  const plain = (await facts(unknown)).querySelectorAll("#now .facts > .fact")[1];
  expect([...plain.querySelectorAll(".fact-detail .fact-ref")].map((ref) => ref.textContent)).toEqual(["B"]);
  expect(plain.querySelector(".fact-detail").textContent).toMatch(/^B · \d+ hours ago$/);
});

test("the plan's proof moves under the plan-now sentence with its change count, and leaves the facts row", async () => {
  const { document, plan } = recordOnlyDelivery();
  plan.changes = [{ id: "c1", at: minute(30), kind: "changed", subjects: ["PAY-2"], by: "lead", why: "Clarified." }];
  const app = await facts(document, plan);
  const now = app.querySelector("#now .plan-now");
  expect(now.querySelector(".plan-now-link").textContent).toBe("4 of 6 criteria proven · plan changed 1 time");
  expect(now.querySelector(".plan-now-link").getAttribute("href")).toBe("#plan");
  expect(app.querySelector("#now .facts .plan-now-link")).toBeNull();
  expect([...app.querySelectorAll("#now .facts > .fact .fact-label")].map((label) => label.textContent).includes("Plan")).toBe(false);
});

test("the facts share one grid through subgrid, stack on a phone, and nothing on the row moves", () => {
  const stylesheet = readFileSync(path.join(CORPUS, "..", "..", "..", "..", "skills", "delivery-views", "assets", "line-map.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = (selector) => (stylesheet.match(new RegExp("(^|\\})\\s*" + selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{([^}]*)\\}", "m")) || [])[2] || "";
  expect(/grid-template-rows:\s*subgrid/.test(rule(".fact"))).toBe(true);
  expect(/grid-row:\s*span 3/.test(rule(".fact"))).toBe(true);
  expect(/column-gap:\s*56px/.test(rule(".facts"))).toBe(true);
  expect(/row-gap:\s*10px/.test(rule(".facts"))).toBe(true);
  expect(/align-self:\s*baseline/.test(rule(".fact-value"))).toBe(true);
  expect(/padding-top:\s*28px/.test(rule(".chapter-now .journey + .facts"))).toBe(true);
  expect(/font:[^;]*var\(--display\)/.test(rule(".fact-count"))).toBe(true);
  // The track's tones are the landed colour, a wash of it and the hairline, so both themes keep it legible.
  expect(/var\(--landed\)/.test(rule(".fact-track .is-landed"))).toBe(true);
  expect(/var\(--landed\)/.test(rule(".fact-track .is-queued"))).toBe(true);
  expect(/var\(--hair\)/.test(rule(".fact-track .is-building"))).toBe(true);
  const phone = stylesheet.slice(stylesheet.indexOf("@media (max-width: 640px)"));
  expect(/\.facts\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/.test(phone)).toBe(true);
  for (const block of stylesheet.match(/[^{}]*\{[^}]*\}/g).filter((one) => /\.fact(-track|-count|-of|-progress|-said)?\b/.test(one.split("{")[0]))) {
    expect([block.split("{")[0].trim(), /animation|transition/.test(block)]).toEqual([block.split("{")[0].trim(), false]);
  }
});

