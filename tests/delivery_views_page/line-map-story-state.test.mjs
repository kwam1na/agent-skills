// Where a story is, on its card: one state line with a dot, green while its lane moves it, amber while it waits, grey
// while the merge queue checks it; before hand-off, how its review ended; and the lane's own words while the lane is on
// the story. The card no longer says proofs left, that it hasn't been handed off, or the summary's count of notes raised.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { ROOT, expect, openPage, pageWithRuns } from "./support.mjs";
import { planFor, unit } from "./plan-fixtures.mjs";

const B = "b".repeat(40);
const LENSES = ["lens.outcome-correctness", "lens.adversarial-testing"];
const NOW = "2026-11-17T10:12:00Z";
const pick = { click: '[data-subject="WORK-1"]' };

/** WORK-1 on lane 1, planned (before hand-off) or `queued` as given, with `journal` after its assignment. */
function record(journal = [], { queued = null, focus = "Writing the tests", focusAt = "2026-11-17T10:00:00Z" } = {}) {
  const entries = [{ id: 1, at: "2026-11-17T09:00:00Z", kind: "lane.assigned", lane: "lane-1", subject: "WORK-1", data: { title: "Work" } }];
  const story = { subject: "WORK-1", title: "Work", lane: "lane-1", stage: "building" };
  const doc = { state: { lanes: [{ id: "lane-1", state: "working", focus, focusAt }],
                         queue: queued ? [{ ...story, order: 1, head: B, ...queued }] : [], planned: queued ? [] : [story] },
                journal: entries, status: { source: "derived", text: "" }, focus: {}, stats: {} };
  journal.forEach((entry) => entries.push({ lane: "lane-1", subject: "WORK-1", ...entry, id: entries.length + 1 }));
  return doc;
}
const started = (round) => ({ at: "2026-11-17T10:01:00Z", kind: "review.started", data: { round, head: B, lenses: LENSES, restart: false } });
const reduced = (round, verdict, findings = {}) => ({ at: "2026-11-17T10:05:00Z", kind: "review.round", data: { round,
  startedAt: "2026-11-17T10:01:00Z", finishedAt: "2026-11-17T10:05:00Z", verdicts: { [LENSES[0]]: "approve", [LENSES[1]]: verdict },
  findings: { P0: 0, P1: 0, P2: 0, P3: 0, ...findings } } });

/** The card's state block as the reader sees it: its tone, its lines and the lane line's own class. */
async function stateOf(doc) {
  const [, shown] = await openPage(pageWithRuns(doc), { now: NOW, steps: [pick] });
  const box = shown.app.querySelector("#detail .d-state");
  return box && { tone: [...box.classList].find((name) => name.startsWith("is-")),
                  lines: [...box.children].map((line) => line.textContent),
                  lane: box.querySelector(".d-lane")?.textContent ?? null, app: shown.app };
}

test("each state before the line says where the story is, with the dot for moving, waiting or checked", async () => {
  // No review round yet: being built, the lane's words second, at the card's soft size.
  const built = await stateOf(record());
  expect([built.tone, built.lines]).toEqual(["is-moving", ["Being built", "Writing the tests · 12m"]]);
  expect(built.lane).toBeNull();
  // A round started and not yet answered.
  expect((await stateOf(record([started(1)]))).lines[0]).toBe("In review, round 1");
  expect((await stateOf(record([started(1), reduced(1, "changes", { P1: 1 }), started(2)]))).lines[0]).toBe("In review, round 2");
  // The last round asked for changes: the story waits on its fixes.
  const fixing = await stateOf(record([started(1), reduced(1, "changes", { P1: 1 })]));
  expect([fixing.tone, fixing.lines[0]]).toEqual(["is-waiting", "Fixing review findings"]);
  // Approved and not handed off: how the review ended, then the lane's words, quieter.
  const ready = await stateOf(record([started(1), reduced(1, "changes", { P1: 1, P3: 2 }), started(2), reduced(2, "approve")],
    { focus: "Full suite on the hand-off head" }));
  expect([ready.tone, ready.lines]).toEqual(["is-moving",
    ["Reviewed, getting ready to hand off", "Approved on round 2 · 3 notes raised", "Full suite on the hand-off head · 12m"]]);
  expect(ready.lane).toBe("Full suite on the hand-off head · 12m");
  expect(ready.app.querySelector("#detail .d-state time").getAttribute("datetime")).toBe("2026-11-17T10:00:00Z");
  // Handed off and waiting on the lead, then checked by the queue.
  const pending = await stateOf(record([started(1), reduced(1, "approve")], { queued: { stage: "queued", pending: true } }));
  expect([pending.tone, pending.lines[0]]).toEqual(["is-waiting", "Waiting for the lead to accept"]);
  for (const stage of ["queued", "checking", "merging"]) {
    const checked = await stateOf(record([], { queued: { stage } }));
    expect([stage, checked.tone, checked.lines[0]]).toEqual([stage, "is-checked", "Being checked before it lands"]);
  }
  // Landed or stopped: no state block; the card says those as it always has.
  expect(await stateOf(record([], { queued: { stage: "merged" } }))).toBeNull();
  expect(await stateOf(record([], { queued: { stage: "stopped" } }))).toBeNull();
});

test("the lane's words show only while the lane is on that story, and the review line only before hand-off", async () => {
  // The lane moved on to another story: its words are that story's, not this one's.
  const moved = record([started(1), reduced(1, "approve"), { at: "2026-11-17T10:06:00Z", kind: "lane.assigned", subject: "WORK-2",
                                                             data: { title: "Next" } }]);
  moved.state.planned.push({ subject: "WORK-2", title: "Next", lane: "lane-1", stage: "building" });
  expect((await stateOf(moved)).lines).toEqual(["Reviewed, getting ready to hand off", "Approved on the first round · nothing raised"]);
  // A round started and then ended by the story being assigned again, with no round reduced, isn't in review.
  expect((await stateOf(record([started(1), { at: "2026-11-17T10:03:00Z", kind: "lane.assigned", data: { title: "Work" } }]))).lines[0])
    .toBe("Being built");
  expect((await stateOf(record([started(1), reduced(1, "changes", { P1: 1 }), started(2),
                                { at: "2026-11-17T10:06:00Z", kind: "lane.assigned", data: { title: "Work" } }]))).lines[0])
    .toBe("Fixing review findings");
  // Words set under 45 seconds ago say "just now".
  expect((await stateOf(record([], { focusAt: "2026-11-17T10:11:40Z" }))).lines[1]).toBe("Writing the tests · just now");
  // A round recorded with no verdicts at all hasn't been approved.
  const silent = reduced(1, "approve");
  silent.data.verdicts = {};
  expect((await stateOf(record([started(1), silent]))).lines[0]).toBe("Fixing review findings");
  // A round restarted under the same number after it was reduced is in review again; a round that answers a start by
  // its id ends it, whatever number it carries.
  expect((await stateOf(record([started(1), reduced(1, "changes", { P1: 1 }), started(1)]))).lines[0]).toBe("In review, round 1");
  const answering = reduced(9, "approve");
  answering.answers = [2];
  expect((await stateOf(record([started(1), answering]))).lines[0]).toBe("Reviewed, getting ready to hand off");
  // The journal's counts, when no report can be read, count every severity.
  expect((await stateOf(record([started(1), reduced(1, "approve", { P0: 1, P1: 1, P2: 1, P3: 1 })]))).lines[1])
    .toBe("Approved on the first round · 4 notes raised");
  // A story still building when the delivery closed isn't in any state any more.
  expect(await stateOf(record([{ at: "2026-11-17T10:10:00Z", kind: "delivery.closed", subject: undefined, lane: undefined, data: {} }]))).toBeNull();
  // No words at all: just the state line.
  expect((await stateOf(record([], { focus: null }))).lines).toEqual(["Being built"]);
  // Handed off: the review line is gone; the lane's words stay while the lane is on it.
  const handed = await stateOf(record([started(1), reduced(1, "approve")], { queued: { stage: "queued", pending: true }, focus: "Handed off" }));
  expect(handed.lines).toEqual(["Waiting for the lead to accept", "Handed off · 12m"]);
});

test("the review line reads the recorded reports: every note fixed, deferred to a follow-up, or still open", async () => {
  const doc = (findings, outcome = "aligned") => {
    const made = record([started(1), reduced(1, "approve", { P2: findings.length }),
                         { at: "2026-11-17T10:05:30Z", kind: "review.recorded", data: { digest: "d".repeat(64), candidateRef: B } }]);
    made.reviewEvidence = { "WORK-1": { candidateRef: B, digest: "d".repeat(64), outcomes: [{ round: 1, outcome: "aligned", entries: [{
      lens: LENSES[0], state: "obtained", binding: { subjectRef: "WORK-1", candidateRef: B, round: 1 }, result: {
        schemaVersion: "review-lens-result/1", lensId: LENSES[0], subjectRef: "WORK-1", candidateRef: B, round: 1, evidence: { observed: true },
        outcome: outcome === "aligned" ? "aligned" : "changes-requested", findings }}] }] } };
    made.reviewEvidence["WORK-1"].outcomes[0].outcome = outcome;
    return made;
  };
  const note = (id, state) => ({ id, severity: "P2", summary: "A note.", state, ...(state === "deferred" ? { deferredIssue: "Later." } : {}) });
  expect((await stateOf(doc([note("F1", "deferred"), note("F2", "deferred")]))).lines[1])
    .toBe("Approved on the first round · 2 notes deferred to a follow-up");
  expect((await stateOf(doc([note("F1", "deferred")]))).lines[1]).toBe("Approved on the first round · 1 note deferred to a follow-up");
  expect((await stateOf(doc([]))).lines[1]).toBe("Approved on the first round · nothing raised");
  // Every lens approved with a note still open: the line says so, never that every note was fixed.
  expect((await stateOf(doc([note("F1", "open")], "unresolved"))).lines[1]).toBe("Approved on the first round · 1 note still open");
  // Two notes raised in round 1 and fixed by round 2, which approved.
  const A = "a".repeat(40);
  const entry = (round, candidate, result) => ({ lens: LENSES[0], state: "obtained", binding: { subjectRef: "WORK-1", candidateRef: candidate, round },
    result: { schemaVersion: "review-lens-result/1", lensId: LENSES[0], subjectRef: "WORK-1", candidateRef: candidate, round, evidence: { observed: true }, ...result } });
  const fixed = record([started(1), reduced(1, "changes", { P2: 2 }), started(2), reduced(2, "approve"),
                        { at: "2026-11-17T10:05:30Z", kind: "review.recorded", data: { digest: "e".repeat(64), candidateRef: B } }]);
  fixed.reviewEvidence = { "WORK-1": { candidateRef: B, digest: "e".repeat(64), outcomes: [
    { round: 1, outcome: "unresolved", entries: [entry(1, A, { outcome: "changes-requested", findings: [note("F1", "open"), note("F2", "open")] })] },
    { round: 2, outcome: "aligned", entries: [entry(2, B, { outcome: "aligned", findings: [], resolvedFindings: [
      { id: "F1", state: "fixed", fixCommit: B, resolution: "Fixed." }, { id: "F2", state: "fixed", fixCommit: B, resolution: "Fixed." }] })] }] } };
  expect((await stateOf(fixed)).lines[1]).toBe("Approved on round 2 · all 2 notes fixed");
  const one = structuredClone(fixed);
  one.reviewEvidence["WORK-1"].outcomes[0].entries[0].result.findings.pop();
  one.reviewEvidence["WORK-1"].outcomes[1].entries[0].result.resolvedFindings.pop();
  expect((await stateOf(one)).lines[1]).toBe("Approved on round 2 · its 1 note fixed");
  // A note its lens withdrew is settled, not fixed.
  const withdrawn = structuredClone(fixed);
  withdrawn.reviewEvidence["WORK-1"].outcomes[1].entries[0].result.resolvedFindings[1] = { id: "F2", state: "withdrawn", resolution: "Not a problem." };
  expect((await stateOf(withdrawn)).lines[1]).toBe("Approved on round 2 · 1 note fixed, 1 dropped");
  // A live push that records a later round before the page has re-read its reports: the reports it has are of an
  // earlier round, so the line says only what the journal counts.
  const newer = structuredClone(fixed);
  newer.journal.push({ id: newer.journal.length + 1, at: "2026-11-17T10:08:00Z", kind: "review.started", lane: "lane-1", subject: "WORK-1",
                       data: { round: 3, head: B, lenses: LENSES, restart: false } },
                     { id: newer.journal.length + 2, at: "2026-11-17T10:09:00Z", kind: "review.round", lane: "lane-1", subject: "WORK-1",
                       data: { round: 3, startedAt: "2026-11-17T10:08:00Z", finishedAt: "2026-11-17T10:09:00Z",
                               verdicts: { [LENSES[0]]: "approve", [LENSES[1]]: "approve" }, findings: { P0: 0, P1: 0, P2: 1, P3: 0 } } },
                     { id: newer.journal.length + 3, at: "2026-11-17T10:09:30Z", kind: "review.recorded", lane: "lane-1", subject: "WORK-1",
                       data: { digest: "f".repeat(64), candidateRef: B } });
  const [, , pushed] = await openPage(pageWithRuns(fixed), { now: NOW, store: { read: "pending" }, steps: [pick, { push: newer }] });
  expect([...pushed.app.querySelector("#detail .d-state").children].map((line) => line.textContent)[1])
    .toBe("Approved on round 3 · 3 notes raised");
});

test("the card drops proofs left, \"hasn't been handed off\" and the summary's count, and keeps the rest", async () => {
  const shown = await stateOf(record([started(1), reduced(1, "approve", { P3: 1 })]));
  const card = shown.app.querySelector("#detail").textContent;
  for (const gone of ["proofs left", "proof left", "hasn't been handed off"]) {
    expect([gone, card.includes(gone)]).toEqual([gone, false]);
  }
  // The summary's own sentence of notes raised is gone; the state block's review line says how the review ended.
  expect(shown.app.querySelector("#detail .run-story")).toBeNull();
  expect(shown.lines[1]).toBe("Approved on the first round · 1 note raised");
  expect(shown.app.querySelector("#detail .d-owner").textContent).toContain("1");
  expect(shown.app.querySelector("#detail .d1").textContent).toBe("Work");
  expect(shown.app.querySelector("#detail .d-meta").textContent).toContain("WORK-1");
});

test("the skill's page says the card's state block", () => {
  const skill = readFileSync(path.join(ROOT, "skills/delivery-views/SKILL.md"), "utf8").replace(/\s+/g, " ");
  expect(skill).toContain("A piece's detail panel says where the story is before it lands, in one state line with a dot");
  expect(skill).toContain("The dot is green while the story's lane moves it, amber while it waits on something and grey while the "
    + "merge queue checks it.");
  expect(skill).toContain("The panel leaves a unit's proofs to the plan chapter, and the review's count of notes raised to the "
    + "recorded reports.");
});

test("a card with a state line keeps a removed or proven unit's standing in its plan row", async () => {
  for (const [units, standing] of [[[unit("WORK-1", { removed: true })], "Removed from the plan"]]) {
    const plan = planFor(units);
    const [, shown] = await openPage(pageWithRuns(record(), {}, { plan }), { now: NOW, steps: [pick] });
    expect(shown.app.querySelector("#detail .d-state")).not.toBeNull();
    expect(shown.app.querySelector("#detail .d-plan-row .pu-standing")?.textContent).toBe(standing);
  }
});
