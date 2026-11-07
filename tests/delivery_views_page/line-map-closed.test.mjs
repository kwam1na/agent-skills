// What a closed delivery's line map claims, pinned so that each of the page's closed-delivery rules turns a row red when
// it breaks: the What's left count's starting number, the journey's ticks, a closed stop's detail, the dots, and the
// wording that must not read as work still to come.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, renderPage } from "./support.mjs";
import { finishLine, item, journal, minute, planFor, record, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

function render(document, plan) {
  return pageWithRuns(document, {}, { config: CONFIG, ...(plan !== undefined ? { plan } : {}) });
}
function open(page, scenario = {}) {
  return openPage(page, { now: NOW, store: false, ...scenario });
}
const landed = (subject, at = 10) => [[at, "approve", subject], [at + 1, "candidate.handed", subject], [at + 2, "candidate.merged", subject]];
const close = [40, "delivery.closed", null];

test("the What's left count starts from the number it showed and eases to the new one", async () => {
  const subjects = ["A", "B", "C", "D", "E"];
  const before = record({ queue: subjects.map((subject) => item(subject, subject === "A" ? "merged" : "queued")), journal: journal(landed("A")) });
  const after = record({ queue: subjects.map((subject) => item(subject, subject === "E" ? "queued" : "merged")),
    journal: journal(subjects.slice(0, 4).flatMap((subject, index) => landed(subject, 10 + index * 5))) });
  const [first, pushed, midway, settled] = await open(render(before), {
    store: {}, frames: true, steps: [{ push: after }, { frame: 150 }, { frame: 900 }] });
  const number = (snapshot) => snapshot.app.querySelector("#left .left-number").firstChild.textContent;
  expect(number(first)).toBe("4");
  // Redrawn with three fewer to go, the count still reads where it stood until a frame moves it, then eases down.
  expect(number(pushed)).toBe("4");
  expect(pushed.app.querySelector("#left .left-number").className).toContain("ticking");
  expect(number(midway)).toBe("3");
  expect(number(settled)).toBe("1");
});

test("on a closed delivery with finish lines, the Pieces landed tick counts plan units that never reached the line", async () => {
  const document = record({ queue: [item("A", "merged")], journal: journal([...landed("A"), close]),
    finish: [{ id: "line", text: "The line is met", met: false }] });
  const plan = planFor([unit("A"), unit("B")], { finish: [finishLine("line", { criteria: [], proofs: [] })] });
  const [shown] = await open(render(document, plan));
  const ticks = [...shown.app.querySelectorAll("#now .journey .tick")].map((tick) => parseFloat(tick.style.left));
  // A landed, B never reached the line, and one finish line: the pieces end two thirds of the way along.
  expect(ticks.map((left) => Math.round(left))).toEqual([0, 67, 100]);
  expect(shown.app.querySelector("#now .journey .stop.pieces").textContent).toBe("Pieces landed");
});

test("a closed stop that was being checked in a batch says nothing of the batch and draws no stage mark", async () => {
  const summary = "Checking Work A, Work B as one batch";
  const steps = [...landed("Z"), [20, "candidate.handed", "A"], [21, "candidate.handed", "B"],
    [22, "check.started", "A", { summary }], [23, "check.started", "B", { summary }]];
  const shape = (entries) => record({ queue: [item("Z", "merged"), item("A", "checking"), item("B", "checking")], journal: journal(entries) });
  const [, live] = await open(render(shape(steps)), { steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  expect(live.app.querySelector("#detail .d-batch").textContent).toBe("Checked together with B; lands in order once the shared test run passes.");
  expect(live.app.querySelector("#detail .d-stage .d-stage-icon")).not.toBeNull();
  const [, closed] = await open(render(shape([...steps, [45, "delivery.closed", null]])), { steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  expect(closed.app.querySelector("#detail .d-batch")).toBeNull();
  expect(closed.app.querySelector("#detail .d-stage .d-stage-icon")).toBeNull();
  expect(closed.app.querySelector("#detail .d-stage").textContent).toBe("Not landed");
});

test("a closed delivery's dots leave out a piece the plan removed, beside a dot for each unit that never reached the line", async () => {
  const document = record({ queue: [item("A", "merged"), item("R", "queued")], journal: journal([...landed("A"), close]) });
  const plan = planFor([unit("A"), unit("R", { removed: true }), unit("N")], { finish: [] });
  const [shown] = await open(render(document, plan));
  const dots = [...shown.app.querySelectorAll("#left .dots i")];
  expect(dots.map((dot) => dot.className)).toEqual(["", "open"]);
  expect(dots[1].style.getPropertyValue("--c")).toBe("var(--soft)");
  expect(shown.app.querySelector("#left .say").textContent).toBe("1 of 2 pieces have landed.");
});

test("a planned piece reads as not landed at the close, whatever stage it claims", async () => {
  const document = record({ queue: [item("A", "merged")], journal: journal([...landed("A"), close]) });
  document.state.planned = [{ subject: "P", title: "Work P", lane: "lane-1", stage: "merged" }];
  const [shown] = await open(render(document));
  const stop = shown.app.querySelector('.stop-btn[data-subject="P"]');
  expect(stop.className.trim()).toBe("stop-btn unlanded");
  expect(stop.getAttribute("aria-label")).toBe("Work P, Not landed");
  expect(shown.app.querySelector("#now .title").textContent).toBe("Closed with 1 of 2 pieces landed.");
});

test("a closed delivery's plan says a unit was never handed off, not that it is before hand-off", async () => {
  const shape = (entries) => {
    const document = record({ queue: [item("A", "merged")], journal: journal(entries) });
    document.state.planned = [{ subject: "B", title: "Work B", lane: "lane-1", stage: "building" }];
    return document;
  };
  const plan = planFor([unit("A"), unit("B")], { finish: [] });
  const row = async (entries) => {
    const [shown] = await open(render(shape(entries), plan));
    return [...shown.app.querySelectorAll("#plan .plan-open li")].map((li) => li.querySelector(".po-next").textContent);
  };
  expect(await row(landed("A"))).toEqual(["Review: not recorded · before hand-off"]);
  const closed = await row([...landed("A"), close]);
  expect(closed).toEqual(["Review: not recorded · never handed off"]);
});

test("a closed delivery names a piece with no lane as having none, and a stop names its lane as a name", async () => {
  const shape = (entries) => record({ queue: [item("A", "merged"), item("U", "queued", { lane: null })], journal: journal(entries) });
  const names = (snapshot) => [...snapshot.app.querySelectorAll("#line .line .name b")].map((name) => name.textContent);
  const [openLine, openStop, laneStop] = await open(render(shape(landed("A"))),
    { steps: [{ click: '.stop-btn[data-subject="U"]' }, { click: '.stop-btn[data-subject="A"]' }] });
  expect(names(openLine)).toEqual(["Lane one", "Not yet assigned"]);
  expect(openStop.app.querySelector("#detail .d-owner").textContent).toBe("Not yet assigned");
  // lane-1 reads as a lane's name, never as a count of lanes.
  expect(laneStop.app.querySelector("#detail .d-owner").textContent).toBe("Lane 1");
  const [closedLine, closedStop] = await open(render(shape([...landed("A"), close])), { steps: [{ click: '.stop-btn[data-subject="U"]' }] });
  expect(names(closedLine)).toEqual(["Lane one", "No lane"]);
  expect(closedStop.app.querySelector("#detail .d-owner").textContent).toBe("No lane");
  expect(closedLine.text).not.toContain("Not yet assigned");
});

// A close that stopped partway: the record's started mark for the closing entry without its finished mark.
const LIVE_NEEDS = (over = {}) => ({ spec: "delivery-needs-you/1", running: true, pass: 3, at: "2026-10-30T11:59:30Z",
  staleAfter: "2026-10-30T12:01:30Z", items: [{ holding: true, source: "host outbox", text: "The host outbox hasn't been acked through journal entry 4",
    clears: "the host posting its outbox" }], ...over });
function closing({ finish = true, marks = "started", earlier = false, needs = null, reopened = false, removed = null, stage = "merged",
  lane = "idle" } = {}) {
  const steps = [...landed("A"), close];
  if (reopened) steps.push([50, "candidate.handed", "A"], [55, "delivery.closed", null]);
  const document = record({ queue: [item("A", stage)], journal: journal(steps),
    finish: [{ id: "line", text: "The line is met", met: finish }] });
  document.state.lanes[0].state = lane;
  const closed = document.journal.filter((entry) => entry.kind === "delivery.closed").at(-1);
  const entry = earlier ? closed.id - 1 : closed.id;
  if (marks !== "none") {
    document.state.meta.closing = { started: { entry, at: closed.at },
      ...(marks === "finished" ? { finished: { entry, at: closed.at } } : {}) };
  }
  if (removed) document.state.planned = [{ subject: "R", title: "Work R", lane: "lane-1", stage: "planned" }];
  const plan = removed ? planFor([unit("A"), unit("R", { removed: removed === "removed" })], { finish: [] }) : null;
  return renderPage(document, { config: CONFIG, needs, plan });
}
const partway = async (page) => (await open(page))[0].app.querySelector("#now .close-partway");

test("a close that stopped partway says so and names a resumed supervisor when the record meets the close condition", async () => {
  const box = await partway(closing());
  expect(box.querySelector(".ah").textContent).toBe("The close stopped partway");
  expect(box.querySelector("p").textContent).toBe("The delivery was closed, but its close-out stopped before it finished. Finish it with lead-run --resume.");
  // The title still says the delivery closed.
  expect((await open(closing()))[0].app.querySelector("#now .title").textContent).toBe("Delivered.");
});

test("a planned piece the plan removed holds nothing, so the close still finishes with a resumed supervisor", async () => {
  expect((await partway(closing({ removed: "removed" }))).querySelector("p").textContent).toContain("Finish it with lead-run --resume.");
  expect((await partway(closing({ removed: "kept" }))).querySelector("p").textContent).toContain("Finish it with lead-close");
});

test("a story left unmerged, or a lane still busy, means only lead-close can finish the close", async () => {
  for (const shape of [{ stage: "stopped" }, { stage: "queued" }, { lane: "working" }, { lane: "blocked" }]) {
    const text = (await partway(closing(shape))).querySelector("p").textContent;
    expect([shape, text.includes("Finish it with lead-close, which doesn't wait for the tracker")]).toEqual([shape, true]);
  }
});

test("a close that stopped partway on a record short of its close condition names lead-close, which doesn't wait for the tracker", async () => {
  const box = await partway(closing({ finish: false }));
  expect(box.querySelector("p").textContent).toBe("The delivery was closed, but its close-out stopped before it finished. "
    + "Finish it with lead-close, which doesn't wait for the tracker; a resumed supervisor would hold on what is still open.");
});

test("a finished close, a record with no marks, a mark for an earlier close and a close still running show no partway box", async () => {
  expect(await partway(closing({ marks: "finished" }))).toBeNull();
  // A finished close with a stopped supervisor's list shows neither: nothing is left for it to do.
  const [stopped] = await open(closing({ marks: "finished", needs: LIVE_NEEDS({ running: false }) }));
  expect(stopped.app.querySelector("#now .needs-you")).toBeNull();
  expect(stopped.app.querySelector("#now .close-partway")).toBeNull();
  expect(await partway(closing({ marks: "none" }))).toBeNull();
  expect(await partway(closing({ earlier: true }))).toBeNull();
  // A live supervisor still closing it: its own list shows what the close waits on, and nothing names a resume.
  const [live] = await open(closing({ needs: LIVE_NEEDS() }));
  expect(live.app.querySelector("#now .close-partway")).toBeNull();
  expect(live.app.querySelector("#now .needs-you .needs-kind").textContent).toBe("Holds the close");
  expect(live.text).not.toContain("lead-run --resume");
  // Its list gone stale, or the supervisor stopped, it reads as stopped partway.
  expect(await partway(closing({ needs: LIVE_NEEDS({ running: false }) }))).not.toBeNull();
  expect(await partway(closing({ needs: LIVE_NEEDS({ staleAfter: "2026-10-30T11:00:00Z" }) }))).not.toBeNull();
});

test("a delivery closed, reopened, closed again and stopped is judged on its last close", async () => {
  expect(await partway(closing({ reopened: true }))).not.toBeNull();
  expect(await partway(closing({ reopened: true, earlier: true }))).toBeNull();
});
