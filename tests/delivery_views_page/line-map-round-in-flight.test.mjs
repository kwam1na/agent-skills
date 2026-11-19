// A review round while it runs: the story's review card says the round is under way at its head and since when, a row
// per lens back or still reading, the lane's reason, the last finished round dated beneath, and earlier notes folded;
// it settles to the finished card when the round is reduced, and a restart, hand-off, settlement or reassignment
// leaves no round shown as running. The bead breathes on the page's tempo, and not at all under reduced motion.
process.env.TZ = "UTC";

import { test } from "node:test";
import { JSDOM } from "jsdom";

import { expect, openPage, pageWithRuns } from "./support.mjs";

const A = "a".repeat(40), B = "b".repeat(40), C = "c".repeat(40);
const LENSES = ["lens.outcome-correctness", "lens.adversarial-testing"];
const NOW = "2026-11-17T10:12:00Z";
const pick = { click: '[data-subject="WORK-1"]' };

const result = (round, candidate, finding) => ({ round, outcome: finding ? "unresolved" : "aligned", entries: [{
  lens: LENSES[0], state: "obtained", binding: { subjectRef: "WORK-1", candidateRef: candidate, round }, result: {
    schemaVersion: "review-lens-result/1", lensId: LENSES[0], subjectRef: "WORK-1", candidateRef: candidate, round, evidence: { observed: true },
    outcome: finding ? "changes-requested" : "aligned", findings: finding ? [{ id: "F1", severity: "P1", summary: "Fix the count.", state: "open" }] : [],
    resolvedFindings: finding ? [] : [{ id: "F1", state: "closed", fixCommit: B, resolution: "The count is fixed." }] } }] });

/** WORK-1 in review on lane 1, with `journal` after its assignment; `earlier` adds two reduced rounds, the second approving B. */
function record(journal, { earlier = false, stage = "building" } = {}) {
  const entries = [{ id: 1, at: "2026-11-17T09:00:00Z", kind: "lane.assigned", lane: "lane-1", subject: "WORK-1", data: { title: "Work" } }];
  const doc = { state: { lanes: [{ id: "lane-1", state: "reviewing" }], queue: [], planned: [{ subject: "WORK-1", title: "Work", lane: "lane-1", stage }] },
                journal: entries, status: { source: "derived", text: "" }, focus: {}, stats: {} };
  if (earlier) {
    const round = (id, n, at) => ({ id, at, kind: "review.round", lane: "lane-1", subject: "WORK-1", data: { round: n, startedAt: at, finishedAt: at,
      verdicts: { [LENSES[0]]: n === 1 ? "changes" : "approve" }, findings: { P0: 0, P1: n === 1 ? 1 : 0, P2: 0, P3: 0 } } });
    entries.push(round(2, 1, "2026-11-17T09:10:00Z"), round(3, 2, "2026-11-17T09:30:00Z"),
                 { id: 4, at: "2026-11-17T09:31:00Z", kind: "review.recorded", lane: "lane-1", subject: "WORK-1", data: { digest: "d".repeat(64), candidateRef: B } });
    doc.reviewEvidence = { "WORK-1": { candidateRef: B, digest: "d".repeat(64), outcomes: [result(1, A, true), result(2, B, false)] } };
  }
  journal.forEach((entry) => entries.push({ lane: "lane-1", subject: "WORK-1", ...entry, id: entries.length + 1 }));
  return doc;
}
const started = (at, data) => ({ at, kind: "review.started", data: { lenses: LENSES, restart: false, ...data } });
const returned = (at, round, lens, verdict, findings = {}) => ({ at, kind: "review.lens.returned",
  data: { round, lens, verdict, findings: { P0: 0, P1: 0, P2: 0, P3: 0, ...findings } } });
const reduced = (at, round, answers) => ({ at, kind: "review.round", answers, data: { round, startedAt: "2026-11-17T10:00:00Z", finishedAt: at,
  verdicts: { [LENSES[0]]: "approve", [LENSES[1]]: "approve" }, findings: { P0: 0, P1: 0, P2: 0, P3: 0 } } });
const rows = (app) => [...app.querySelectorAll(".run-card.is-running .run-lens-row")].map((row) =>
  [row.getAttribute("data-lens"), row.classList.contains("is-back") ? "back" : "reading", row.textContent]);

test("a first round under way shows its head, since when, the lane's reason and each lens reading, and updates in place", async () => {
  const doc = record([started("2026-11-17T10:00:00Z", { round: 1, head: A, why: "First look at the count." })]);
  const back = structuredClone(doc);
  back.journal.push({ id: 3, at: "2026-11-17T10:08:00Z", kind: "review.lens.returned", lane: "lane-1", subject: "WORK-1",
                      data: { round: 1, lens: LENSES[1], verdict: "changes", findings: { P0: 0, P1: 2, P2: 0, P3: 0 } } });
  const [, shown, updated] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick, { push: back }] });
  const app = shown.app;
  expect(app.querySelector(".run-story").textContent).toBe("In review: round 1 under way at aaaaaaa, 0 of 2 lenses back.");
  expect(app.querySelector(".run-last")).toBeNull();
  expect(app.querySelectorAll(".run-bead").length).toBe(1);
  expect(app.querySelector(".run-bead.is-running .run-pick").getAttribute("aria-pressed")).toBe("true");
  expect(app.querySelector(".run-card.is-running .run-card-outcome").textContent).toBe("0 of 2 back");
  expect(app.querySelector(".run-reading-at").textContent).toBe("Reviewing aaaaaaa · started 12 min ago");
  expect(app.querySelector(".run-why").textContent).toBe("First look at the count.");
  expect(rows(app).map(([lens, state]) => [lens, state])).toEqual([[LENSES[0], "reading"], [LENSES[1], "reading"]]);
  expect(rows(app)[0][2]).toContain("reading · 12 min");
  // The lens that returns settles in place, with its verdict and notes, and the chip counts it; nothing reloads.
  expect(updated.app.querySelector(".run-card.is-running .run-card-outcome").textContent).toBe("1 of 2 back");
  const [, [, state, said]] = rows(updated.app);
  expect(state).toBe("back");
  expect(said).toContain("asked for changes");
  expect(said).toContain("2 serious notes");
  expect(rows(updated.app)[0][1]).toBe("reading");
});

test("no reason given shows no reason, and the reduced round settles the card", async () => {
  const doc = record([started("2026-11-17T10:00:00Z", { round: 1, head: A })]);
  const done = structuredClone(doc);
  done.journal.push({ id: 3, lane: "lane-1", subject: "WORK-1", ...reduced("2026-11-17T10:10:00Z", 1, [2]) });
  const [, shown, settled] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick, { push: done }] });
  expect(shown.app.querySelector(".run-card.is-running")).not.toBeNull();
  expect(shown.app.querySelector(".run-why")).toBeNull();
  expect(settled.app.querySelector(".run-card.is-running")).toBeNull();
  expect(settled.app.querySelector(".run-bead.is-running")).toBeNull();
  expect(settled.app.textContent).not.toContain("under way");
});

test("over finished rounds, the last is dated beneath with the commits since, and its confirmed notes fold", async () => {
  const doc = record([started("2026-11-17T10:00:00Z", { round: 3, head: C, moved: 11 })], { earlier: true });
  const [, running, opened, finished] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick, { click: ".run-earlier > summary" },
                                                                                                 { click: ".run-pick", index: 1 }] });
  const app = running.app;
  expect(app.querySelector(".run-story").textContent).toBe("In review: round 3 under way at ccccccc, 0 of 2 lenses back.");
  expect(app.querySelector(".run-last").textContent).toBe("Round 2 approved bbbbbbb · 11 commits since");
  expect(app.querySelectorAll(".run-bead").length).toBe(3);
  expect(app.querySelector(".run-card.is-running .run-reading-at").textContent).toContain("Reviewing ccccccc");
  // The notes round 2 confirmed are one line while the round runs, and open as they do on the finished card.
  const fold = app.querySelector(".run-card.is-running .run-earlier");
  expect(fold.hasAttribute("open")).toBe(false);
  expect(fold.querySelector("summary").textContent).toContain("Round 2 confirmed 1 earlier note: 1 fixed");
  expect(opened.app.querySelector(".run-earlier").hasAttribute("open")).toBe(true);
  expect(opened.app.querySelector(".run-earlier .run-closures .run-finding-text").textContent).toBe("Fix the count.");
  // A finished round, picked, shows its card as it does today.
  expect(finished.app.querySelector(".run-card.is-running")).toBeNull();
  expect(finished.app.querySelector(".run-card-outcome").textContent).toBe("Approved");
  expect(finished.app.querySelector(".run-closures .run-finding-text").textContent).toBe("Fix the count.");
});

test("over earlier evidence that is stale, the round in flight still shows as under way, with a note", async () => {
  const doc = record([started("2026-11-17T10:00:00Z", { round: 3, head: C, moved: 11 })], { earlier: true });
  doc.reviewEvidence["WORK-1"].digest = "e".repeat(64);
  const [, shown] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick] });
  expect(shown.app.querySelector(".run-card.is-running .run-card-outcome").textContent).toBe("0 of 2 back");
  expect(shown.app.querySelector(".d-run-none").textContent).toBe("Earlier rounds' evidence is unavailable or stale for this candidate.");
  // With no valid earlier rounds, nothing is dated beneath the headline.
  expect(shown.app.querySelector(".run-last")).toBeNull();
});

test("the reduced round's bead settles when a redraw first finds it reduced, for that one motion, and not after", async () => {
  // The page as rendered after round 3 was reduced and kept; the record pushed first still has it in flight.
  const running = record([started("2026-11-17T10:00:00Z", { round: 3, head: C, moved: 11 })], { earlier: true });
  const kept = structuredClone(running);
  const start = kept.journal.at(-1).id;
  kept.journal.push({ id: start + 1, lane: "lane-1", subject: "WORK-1", ...reduced("2026-11-17T10:10:00Z", 3, [start]) },
                    { id: start + 2, at: "2026-11-17T10:11:00Z", kind: "review.recorded", lane: "lane-1", subject: "WORK-1",
                      data: { digest: "f".repeat(64), candidateRef: C } });
  const third = result(3, C, false);
  third.entries[0].result.resolvedFindings = [];
  kept.reviewEvidence["WORK-1"] = { candidateRef: C, digest: "f".repeat(64), outcomes: [result(1, A, true), result(2, B, false), third] };
  // The page's own clock runs here: the settle lasts the one motion, so a redraw past it shows the bead still.
  const [, inFlight, settled, again] = await openPage(pageWithRuns(kept), { steps: [pick, { push: running }, { push: kept, wait: 800 },
                                                                                    { push: kept }] }).then((snaps) => snaps.slice(1));
  expect(inFlight.app.querySelector(".run-bead.is-running")).not.toBeNull();
  const settling = (snap) => [...snap.app.querySelectorAll(".run-bead")].map((bead) => bead.classList.contains("is-settling"));
  expect(settling(settled)).toEqual([false, false, true]);
  // Reduced and approved, the card's state line says the review ended, so the summary's count is left to the reports.
  expect(settled.app.querySelector("#detail .d-state .d-now").textContent).toBe("Reviewed, getting ready to hand off");
  expect(settled.app.querySelector(".run-story")).toBeNull();
  expect(settling(again)).toEqual([false, false, false]);
});

test("a restart, a hand-off, a settlement or a reassignment leaves no round shown as running", async () => {
  const first = started("2026-11-17T10:00:00Z", { round: 1, head: A });
  // After a restart's new start, only its own lenses count: the old start's return is gone.
  const restarted = record([first, returned("2026-11-17T10:04:00Z", 1, LENSES[0], "approve"),
                            started("2026-11-17T10:05:00Z", { round: 1, head: A, restart: true })]);
  const [, again] = await openPage(pageWithRuns(restarted), { now: NOW, steps: [pick] });
  expect(again.app.querySelector(".run-card.is-running .run-card-outcome").textContent).toBe("0 of 2 back");
  expect(again.app.querySelector(".run-reading-at").textContent).toContain("started 7 min ago");
  const handed = { at: "2026-11-17T10:06:00Z", kind: "candidate.handed", data: { head: A, changeRequest: "https://example.test/pull/1", rounds: 0, checks: [] } };
  const assigned = { at: "2026-11-17T10:06:00Z", kind: "lane.assigned", lane: "lane-2", data: { title: "Work" } };
  for (const [name, doc] of [["handed", record([first, handed])], ["reassigned", record([first, assigned])],
                             ["settled", record([first], { stage: "settled" })]]) {
    const [, shown] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick] });
    expect([name, shown.app.querySelector(".run-bead.is-running")]).toEqual([name, null]);
    expect([name, shown.app.textContent.includes("under way")]).toEqual([name, false]);
  }
});

test("the card's headline is the story's review wait as the record says it, and follows it in place", async () => {
  const said = (back) => ({ subject: "WORK-1", on: "lane-1", cause: "review", stage: "review", frees: "ended", since: "2026-11-17T10:00:00Z",
                            needsSomeone: false, words: `In review: round 1 under way at aaaaaaa, ${back} of 2 lenses back.` });
  const doc = record([started("2026-11-17T10:00:00Z", { round: 1, head: A })]);
  doc.waits = [said(0)];
  const back = structuredClone(doc);
  back.journal.push({ id: 3, at: "2026-11-17T10:08:00Z", kind: "review.lens.returned", lane: "lane-1", subject: "WORK-1",
                      data: { round: 1, lens: LENSES[1], verdict: "approve", findings: { P0: 0, P1: 0, P2: 0, P3: 0 } } });
  back.waits = [{ ...said(1), subject: "WORK-2", words: "Another story's wait." }, said(1),
                { ...said(1), cause: "order", words: "An order wait." }];
  const [, shown, updated] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick, { push: back }] });
  expect(shown.app.querySelector(".run-story").textContent).toBe("In review: round 1 under way at aaaaaaa, 0 of 2 lenses back.");
  expect(updated.app.querySelector(".run-story").textContent).toBe("In review: round 1 under way at aaaaaaa, 1 of 2 lenses back.");
  // Once the round is reduced the card settles, whatever wait a stale record still carries.
  const done = structuredClone(back);
  done.journal.push({ id: 4, lane: "lane-1", subject: "WORK-1", ...reduced("2026-11-17T10:10:00Z", 1, [2]) });
  const [, , settled] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick, { push: done }] });
  expect(settled.app.querySelector(".run-card.is-running")).toBeNull();
});

test("the round in flight's bead breathes on the page's tempo, and nothing moves under reduced motion", () => {
  const dom = new JSDOM(pageWithRuns(record([started("2026-11-17T10:00:00Z", { round: 1, head: A })])));
  const moving = [];
  const walk = (rules, media) => {
    for (const rule of rules) {
      if (rule.cssRules && rule.media) { walk(rule.cssRules, rule.media.mediaText); continue; }
      if (rule.selectorText && /is-running|is-settling|run-earlier/.test(rule.selectorText)
          && (rule.style.getPropertyValue("animation") || rule.style.getPropertyValue("transition"))) {
        moving.push([rule.selectorText, media, rule.style.getPropertyValue("animation")]);
      }
    }
  };
  for (const sheet of dom.window.document.styleSheets) { walk(sheet.cssRules, null); }
  dom.window.close();
  expect(moving.length > 0).toBe(true);
  for (const [selector, media, animation] of moving) {
    // Every motion is asked for by the reader's own setting, and what loops counts whole beats.
    expect([selector, media]).toEqual([selector, "(prefers-reduced-motion: no-preference)"]);
    if (/infinite/.test(animation)) { expect([selector, /calc\(var\(--beat[^)]*\) \* 2\)/.test(animation)]).toEqual([selector, true]); }
  }
  expect(moving.some(([selector, , animation]) => selector.includes(".run-bead.is-running") && /infinite/.test(animation))).toBe(true);
});
