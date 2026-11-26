// The Now page leads with one sentence naming where the delivery stands and who it waits on, four marks with the live
// one labelled by its holder, and at most one fact that changes what the reader would do. Each of the twelve stages and
// the three overrides is a fixture record under tests/fixtures/delivery-views/now/, drawn here and held to what the
// fixture says Now must show; anything that holds nothing the reader can act on never reaches it.
process.env.TZ = "UTC";

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { expect, openPage, pageWithRuns } from "./support.mjs";
import { CONFIG, NOW_FIXTURES } from "./now-fixtures.mjs";

const FIXTURES = readdirSync(NOW_FIXTURES).filter((name) => name.endsWith(".json")).sort()
  .map((name) => JSON.parse(readFileSync(path.join(NOW_FIXTURES, name), "utf8")));

/** The page for a fixture, with its Needs you list and the forge's facts embedded as the renderer embeds them. */
export function fixturePage(fixture) {
  const blocks = [["delivery-needs", fixture.needs], ["delivery-integration", fixture.integration]]
    .filter(([, value]) => value !== null)
    .map(([id, value]) => `<script type="application/json" id="${id}">${JSON.stringify(value)}</script>\n`).join("");
  return pageWithRuns(fixture.document, {}, { config: CONFIG }).replace("<script>\n", blocks + "<script>\n");
}
/** What Now shows, read from the page: its sentence, its link, its marks and its fact. */
export function nowOf(app) {
  const box = app.querySelector("#now .now-state");
  const sentence = box.querySelector(".now-sentence");
  const link = sentence.querySelector("a");
  const fact = box.querySelector(".now-fact");
  return {
    sentence: sentence.textContent,
    link: link ? [link.textContent, link.getAttribute("href")] : null,
    stop: sentence.classList.contains("is-stop"),
    marks: [...box.querySelectorAll(".now-mark")].map((mark) => [mark.querySelector(".now-mark-label").textContent, mark.getAttribute("data-state"),
      mark.getAttribute("data-who")]),
    fact: fact ? fact.textContent : null,
    factStop: fact ? fact.classList.contains("is-stop") : false,
  };
}
async function drawn(fixture) {
  const [first] = await openPage(fixturePage(fixture), { now: fixture.now, store: false });
  return first.app;
}

test("there is a fixture for every one of the twelve stages and the three overrides", () => {
  expect(FIXTURES.length).toBe(16);
  expect(new Set(FIXTURES.map((fixture) => fixture.name.slice(0, 2))).size).toBe(16);
});

for (const fixture of FIXTURES) {
  test(`${fixture.title}: Now says the stage, marks it with its holder and states its fact`, async () => {
    const app = await drawn(fixture);
    const shown = nowOf(app);
    // A mark's label names its holder after a middle dot only when that changes what the reader does: you, or the
    // forge. The lead and the lanes go unnamed; every holder stays in the mark's data-who.
    const named = (who) => who === "you" || who === "forge";
    const expected = { ...fixture.expected, marks: fixture.expected.marks.map(([label, state, who]) => [named(who) ? `${label} · ${who}` : label, state, who]) };
    expect(shown).toEqual(expected);
    // Never more than the four marks the canvas draws.
    expect(shown.marks.length <= 4).toBe(true);
    // Now leads the chapter: nothing but the delivery's kicker and name come before it.
    const order = [...app.querySelector("#now .inner").children].map((node) => node.className);
    expect(order.indexOf("now-state")).toBe(order.indexOf("title") + 1);
  });
}

test("a tracker behind the record, with nothing else holding, is named nowhere in Now", async () => {
  const lag = FIXTURES.find((fixture) => fixture.name === "16-tracker-lag-only");
  const app = await drawn(lag);
  const box = app.querySelector("#now .now-state").textContent;
  expect(box).not.toContain("tracker");
  expect(box).not.toContain("holding up the finish");
  // Needs you keeps the item, below Now.
  expect(app.querySelector("#now .needs-you").textContent).toContain("The tracker is behind the record");
});

test("while stories land the lanes are rows whose threads curve into the one finish, and later stages stay one thread", async () => {
  const landing = await drawn(FIXTURES.find((fixture) => fixture.name === "01-stories-landing"));
  const marks = landing.querySelector("#now ol.now-marks");
  // A list holds only items: the drawing is an item of its own, hidden from a screen reader, one curve per lane and the
  // run to the finish.
  expect(marks.classList.contains("is-merge")).toBe(true);
  expect([...marks.children].every((child) => child.tagName === "LI")).toBe(true);
  const threads = marks.querySelector("li.now-threads");
  expect([threads.getAttribute("aria-hidden"), threads.querySelectorAll("svg path.now-thread").length]).toEqual(["true", 4]);
  // The finish stands where the threads meet: beside every lane row, and the sentence starts on its line.
  expect(marks.querySelector("li.is-finish").style.gridRow).toBe("1 / span 3");
  expect(landing.querySelector("#now .now-state").style.getPropertyValue("--merge-mid")).toBe("36px");
  // A stopped lane's thread, like a paused one, is drawn in the stop colour.
  const stopped = await drawn(FIXTURES.find((fixture) => fixture.name === "14-work-stopped"));
  expect(stopped.querySelectorAll("#now .now-thread.is-held").length > 0).toBe(true);
  // The rejoin's stages run one after another: one thread, no rows.
  const suite = await drawn(FIXTURES.find((fixture) => fixture.name === "03-suite-running"));
  expect([suite.querySelector("#now .now-marks.is-merge"), suite.querySelector("#now .now-threads")]).toEqual([null, null]);
});

test("a stop says itself once: only the words naming it carry the stop colour, and the fact under it stays quiet", async () => {
  const stopped = (await drawn(FIXTURES.find((fixture) => fixture.name === "14-work-stopped"))).querySelector("#now .now-sentence");
  // The words before the colon are the stop's own; the sentence reads the same as it did whole.
  expect([...stopped.querySelectorAll(".now-stop-lead")].map((node) => node.textContent)).toEqual(["The queue has stopped"]);
  expect(stopped.textContent).toBe("The queue has stopped: the main line is broken. B3 is the repair.");
  // A calm sentence over a stop fact has no such words: the fact keeps the stop colour, since nothing above says it.
  const stale = (await drawn(FIXTURES.find((fixture) => fixture.name === "15-out-of-date"))).querySelector("#now .now-sentence");
  expect(stale.querySelector(".now-stop-lead")).toBe(null);
  const css = readFileSync(path.resolve(NOW_FIXTURES, "../../../../skills/delivery-views/assets/line-map.css"), "utf8");
  expect(css).toContain(".now-sentence.is-stop:has(.now-stop-lead) { color: var(--ink); }");
  expect(css).toContain(".now-stop-lead { display: block; margin-bottom: 2px; color: var(--held); font-weight: 500; }");
  // The colon between the stop's name and its reason is heard, not seen: the name stands on its own line.
  expect([...stopped.querySelectorAll(".sr")].map((node) => node.textContent)).toEqual([": "]);
  expect(css).toContain(".now-sentence.is-stop ~ .now-fact.is-stop { color: var(--faint); }");
});

test("under a stop the marks agree with the sentence, and what sits below Now steps back", async () => {
  const stopped = await drawn(FIXTURES.find((fixture) => fixture.name === "14-work-stopped"));
  // The live mark is paused where it stands, and says so to a screen reader; its state is still live.
  const paused = [...stopped.querySelectorAll("#now .now-mark.is-paused")];
  expect(paused.map((mark) => [mark.querySelector(".now-mark-label").textContent, mark.getAttribute("data-state"),
    mark.querySelector(".sr").textContent])).toEqual([["Lane 1 · 2 of 4", "live", ", paused"]]);
  // Pieces waiting their turn under a stop sit in a quiet well, not on a sheet.
  expect([...stopped.querySelectorAll("#now .checking")].map((panel) => [panel.classList.contains("live"), panel.classList.contains("is-held")]))
    .toEqual([[false, true]]);
  // A calm page pauses nothing.
  const calm = await drawn(FIXTURES.find((fixture) => fixture.name === "01-stories-landing"));
  expect(calm.querySelector("#now .now-mark.is-paused")).toBe(null);
  // A Needs you list the page can't show is a notice, drawn as one line; its words are unchanged.
  const stale = await drawn(FIXTURES.find((fixture) => fixture.name === "15-out-of-date"));
  const notice = stale.querySelector("#now .needs-you");
  expect([notice.classList.contains("is-unavailable"), notice.querySelector(".needs-none").textContent])
    .toEqual([true, "Hidden while updates are stopped, until the lead restarts them."]);
  const css = readFileSync(path.resolve(NOW_FIXTURES, "../../../../skills/delivery-views/assets/line-map.css"), "utf8");
  expect(css).toContain(".now-mark.is-paused .now-dot { border: 0; background: var(--paper); box-shadow: none; }");
  expect(css).toContain(".now-mark.is-paused .now-dot::before, .now-mark.is-paused .now-dot::after { content: \"\"; position: absolute;");
  expect(css).toContain(".needs-you.is-sheet.is-unavailable::after { content: none; }");
  expect(css).toContain(".checking.is-held::after { background: var(--well); box-shadow: none; }");
});

test("Now names no command, branch the record doesn't give, or check of this repository in any state", async () => {
  for (const fixture of FIXTURES) {
    const text = (await drawn(fixture)).querySelector("#now .now-state").textContent;
    expect(text).not.toMatch(/\blead-[a-z]+|--[a-z]|run_delivery|python|scripts\//);
  }
});

test("a stale record with a stopped supervisor puts the stop-colour fact over any stage, the way it does over the merge", async () => {
  const stopped = FIXTURES.find((fixture) => fixture.name === "15-out-of-date");
  for (const name of ["02-last-landed", "06-review-running", "01-stories-landing"]) {
    const base = FIXTURES.find((fixture) => fixture.name === name);
    const shown = nowOf(await drawn({ ...base, needs: stopped.needs }));
    expect([shown.fact, shown.factStop]).toEqual([stopped.expected.fact, true]);
    expect(shown.sentence).toBe(base.expected.sentence);
  }
  // A record with no update for over an hour and a half says so, the same way, with no supervisor list at all.
  const stale = FIXTURES.find((fixture) => fixture.name === "08-waits-for-merge");
  const old = { ...stale, integration: null, document: { ...stale.document, state: { ...stale.document.state,
    meta: { ...stale.document.state.meta, updatedAt: "2026-10-30T09:00:00Z" } } } };
  const shown = nowOf(await drawn(old));
  expect([shown.fact, shown.factStop]).toEqual(["No update for 3 hours · may be out of date", true]);
});

test("the count line and the drift sentence the old top of Now carried are gone", async () => {
  for (const fixture of FIXTURES) {
    const app = await drawn(fixture);
    expect(app.querySelector("#now .now-count")).toBe(null);
    expect(app.querySelector("#now .say-drift")).toBe(null);
    expect(app.querySelector("#now").textContent).not.toContain("the gap is");
  }
});

test("a failing check the target passes says so, and so does a mix", async () => {
  const merge = FIXTURES.find((fixture) => fixture.name === "08-waits-for-merge");
  const fact = async (failing) => nowOf(await drawn({ ...merge, integration: { checks: { pending: 0, failing } } })).fact;
  expect(await fact([{ name: "lint", onTarget: false }])).toBe("1 forge check fails, and main passes it.");
  expect(await fact([{ name: "lint", onTarget: true }, { name: "e2e", onTarget: false }])).toBe("2 forge checks fail, 1 of them on main too.");
  expect(await fact([{ name: "lint", onTarget: true }, { name: "e2e", onTarget: true }])).toBe("2 forge checks fail, on main too.");
  expect(await fact([])).toBe(null);
});

test("the reader's call goes to the lane that asks while stories land, and its fact names that lane", async () => {
  const landing = FIXTURES.find((fixture) => fixture.name === "01-stories-landing");
  const call = FIXTURES.find((fixture) => fixture.name === "13-needs-your-call");
  const shown = nowOf(await drawn({ ...landing, needs: call.needs }));
  expect(shown.sentence).toBe(call.expected.sentence);
  // The lanes read in lane-number order, side by side into the rejoin.
  expect(shown.marks).toEqual([["Lane 1 · 2 of 4", "live", null], ["Lane 2 · 2 of 3 · you", "live", "you"],
    ["Lane 3 · 2 of 2", "done", null], ["Rejoin", "todo", null]]);
  expect(shown.fact).toBe("Lane 2 waits on you.");
});

test("a delivery closed with stories unlanded draws no lane at work and no rejoin done", async () => {
  const landing = FIXTURES.find((fixture) => fixture.name === "01-stories-landing");
  const journal = landing.document.journal;
  const closed = { ...landing, document: { ...landing.document, journal: [...journal,
    { id: journal.length + 1, at: "2026-10-30T11:00:00Z", kind: "delivery.closed", lane: null, subject: "APP-10", data: {} }] } };
  const shown = nowOf(await drawn(closed));
  expect(shown.sentence).toBe("Closed Oct 30: 6 of 9 stories landed.");
  expect(shown.marks.map(([, state]) => state)).toEqual(["todo", "todo", "done", "todo"]);
});

/** A fixture by name, with entries appended to its journal under the delivery's key and its meta changed. */
const KEY = "APP-10";
function derived(name, { entries = [], meta = {}, queue = null, needs, integration } = {}) {
  const base = FIXTURES.find((fixture) => fixture.name === name);
  const journal = [...base.document.journal];
  entries.forEach(([kind, data]) => journal.push({ id: journal.length + 1, at: "2026-10-30T11:50:00Z", kind, lane: null, subject: KEY, data }));
  const state = { ...base.document.state, meta: { ...base.document.state.meta, ...meta }, ...(queue ? { queue: queue(base.document.state.queue) } : {}) };
  return { ...base, document: { ...base.document, state, journal }, ...(needs !== undefined ? { needs } : {}),
    ...(integration !== undefined ? { integration } : {}) };
}
const head = (fixture) => [...fixture.document.journal].reverse().find((entry) => entry.kind === "integration.merged")?.data.head;

test("a review round answered by its record is no longer running, and a start at an older head never is", async () => {
  const fixing = FIXTURES.find((fixture) => fixture.name === "07-review-fix");
  const at = fixing.document.journal.find((entry) => entry.kind === "review.round").data.head;
  // The lead moved the head after the round asked for a fix: a start naming the old head is not a round under way.
  const moved = derived("07-review-fix", { entries: [["integration.merged", { target: "main", targetHead: "f".repeat(40), head: "e".repeat(40), conflicts: [] }],
    ["review.started", { round: 2, head: at, lenses: ["lens.outcome-correctness"], restart: false }]] });
  expect(nowOf(await drawn(moved)).sentence).not.toContain("is in round");
  expect(nowOf(await drawn(moved)).sentence).toBe("All 9 stories are in. The full suite runs next.");
});

test("a held queue says so in the stop colour, with what nothing merges until", async () => {
  const shown = nowOf(await drawn(derived("01-stories-landing", { meta: { hold: { reason: "Waiting on the deploy freeze." } } })));
  expect([shown.sentence, shown.stop, shown.fact, shown.factStop])
    .toEqual(["The queue is paused: Waiting on the deploy freeze.", true, "Nothing merges until the hold lifts.", true]);
});

test("the overrides combine as the canvas says: a broken base stops the rejoin's live mark, a stop fact outlasts a call, a call wakes a todo mark", async () => {
  const broken = nowOf(await drawn(derived("08-waits-for-merge", { entries: [["base.broken", { source: "queue" }]] })));
  expect(broken.marks[2]).toEqual(["Merge · you", "stop", "you"]);
  expect([broken.sentence, broken.stop]).toEqual(["The queue has stopped: the main line is broken. No repair is named yet.", true]);
  expect(broken.fact).toBe("Nothing merges until a repair is named.");
  const call = FIXTURES.find((fixture) => fixture.name === "13-needs-your-call");
  const stopped = nowOf(await drawn({ ...derived("14-work-stopped"), needs: call.needs }));
  expect(stopped.sentence).toBe(call.expected.sentence);
  expect([stopped.fact, stopped.factStop]).toEqual(["Nothing merges until the repair lands.", true]);
  // Nothing landed and nothing started: every lane is to come, and the call makes the asking lane's mark live and yours.
  const base = FIXTURES.find((fixture) => fixture.name === "01-stories-landing");
  const planned = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "C1", "C2"].map((subject) => ({ subject, title: `Work ${subject}`,
    lane: { A: "lane-1", B: "lane-2", C: "lane-3" }[subject[0]], stage: "planned" }));
  const fresh = { ...base, needs: null, document: { ...base.document, journal: [], state: { ...base.document.state, queue: [], planned } } };
  // Without the call every lane is to come, so the asking lane's mark starts todo.
  expect(nowOf(await drawn(fresh)).marks.map(([, state]) => state)).toEqual(["todo", "todo", "todo", "todo"]);
  const woke = nowOf(await drawn({ ...fresh, needs: call.needs }));
  expect(woke.marks.find(([label]) => label.startsWith("Lane 2"))).toEqual(["Lane 2 · 0 of 3 · you", "live", "you"]);
  expect(woke.marks.filter(([, state]) => state === "todo")).toHaveLength(3);
});

test("the target moving yields to a suite at the new head, and a silent supervisor says when it last reported", async () => {
  const moved = FIXTURES.find((fixture) => fixture.name === "10-target-moved");
  const running = derived("10-target-moved", { entries: [["integration.suite.started", { head: head(moved), startedAt: "2026-10-30T11:50:00Z" }]] });
  expect(nowOf(await drawn(running)).sentence).toBe("All 9 stories are in. The full suite is running: 10 min.");
  const silent = derived("08-waits-for-merge", { integration: null, needs: { spec: "delivery-needs-you/1", running: true, at: "2026-10-30T11:40:00Z",
    staleAfter: "2026-10-30T11:45:00Z", items: [] } });
  expect(nowOf(await drawn(silent)).fact).toBe("Last update 20 minutes ago · may be out of date");
});

test("a forge block with a malformed count draws nothing of the forge, and a passing suite with no round sends the review next", async () => {
  const malformed = nowOf(await drawn(derived("08-waits-for-merge", { integration: { checks: { pending: -1, failing: [{ name: "lint", onTarget: true }] } } })));
  expect([malformed.sentence, malformed.fact]).toEqual(["All 9 stories are in. #7 waits for your merge.", null]);
  const passed = FIXTURES.find((fixture) => fixture.name === "04-suite-failed");
  const suite = passed.document.journal.find((entry) => entry.kind === "integration.suite");
  const green = { ...passed, document: { ...passed.document, journal: passed.document.journal.map((entry) => entry === suite ? { ...entry, data: { ...entry.data, result: "passed" } } : entry) } };
  const shown = nowOf(await drawn(green));
  expect([shown.sentence, shown.marks[1]]).toEqual(["The full suite passed. The integration review runs next.", ["Review", "live", "lead"]]);
});

test("a delivery closed with every story in but never rejoined draws its Rejoin mark to come", async () => {
  const landed = FIXTURES.find((fixture) => fixture.name === "02-last-landed");
  const journal = landed.document.journal;
  const closed = { ...landed, document: { ...landed.document, journal: [...journal,
    { id: journal.length + 1, at: "2026-10-30T11:00:00Z", kind: "delivery.closed", lane: null, subject: "APP-10", data: {} }] } };
  const shown = nowOf(await drawn(closed));
  expect(shown.marks.at(-1)).toEqual(["Rejoin", "todo", null]);
  expect(shown.marks.slice(0, -1).every(([, state]) => state === "done")).toBe(true);
});
