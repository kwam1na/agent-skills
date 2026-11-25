// Journal kinds the line map names. A base re-run that was stopped, not failed, reads as stopped in the piece's merge
// check, never as a verdict, and the feed draws it. A check carried over a base move reads as carried.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

test("a stopped base rerun is drawn in the story and reads as stopped in the piece's merge check", async () => {
  const document = record({
    queue: [item("A", "queued")],
    journal: journal([[10, "candidate.handed", "A"], [11, "check.started", "A"], [12, "check.failed", "A"],
      [13, "base.rerun.started", "A"], [14, "base.rerun.stopped", "A"]]),
  });
  const [shown, card] = await openPage(pageWithRuns(document, {}, { config: CONFIG }),
    { now: NOW, store: false, steps: [{ click: ':is(.stop-btn, .mg-tick)[data-subject="A"]' }] });
  // The story draws the entry, and the merge check takes its kind's label from the story.
  expect(shown.app.querySelector(`#happened .beat > time[datetime="${document.journal.at(-1).at}"]`) !== null).toBe(true);
  const history = card.app.querySelector("#detail .mh");
  expect(history.querySelector("summary .mh-sub").textContent).toMatch(/^ · Try stopped /);
  expect([...history.querySelectorAll("li span")].map((row) => row.textContent).at(-1)).toBe("The run on the main line stopped: After 4s, for the piece at 0000034. The stop exited 143.");
});

test("a check carried over a base move is drawn in the story and reads as carried in the piece's merge check", async () => {
  const document = record({
    queue: [item("A", "checking")],
    journal: journal([[10, "candidate.handed", "A"], [11, "check.started", "A"], [12, "check.finished", "A"],
      [13, "candidate.stopped", "A"], [14, "check.started", "A"], [15, "check.carried", "A"]]),
  });
  const [shown, card] = await openPage(pageWithRuns(document, {}, { config: CONFIG }),
    { now: NOW, store: false, steps: [{ click: ':is(.stop-btn, .mg-tick)[data-subject="A"]' }] });
  // The story draws the entry, and the merge check takes its kind's label from the story.
  expect(shown.app.querySelector(`#happened .beat > time[datetime="${document.journal.at(-1).at}"]`) !== null).toBe(true);
  const history = card.app.querySelector("#detail .mh");
  expect(history.querySelector("summary .mh-sub").textContent).toMatch(/^ · Checks carried /);
  expect([...history.querySelectorAll("li span")].map((row) => row.textContent).at(-1)).toBe("Checks carried to the new main line: lib carried, none ran again, across 1 commit");
});

test("the target settling reads as its kind's label in the story, never as its kind's name", async () => {
  const document = record({ queue: [item("A", "queued")], journal: journal([[10, "candidate.handed", "A"],
    [11, "target.settled", "A", { data: { target: "main", peers: ["B-1"], stories: ["B-2"], seconds: 600 } }]]) });
  const [shown] = await openPage(pageWithRuns(document, {}, { config: CONFIG }), { now: NOW, store: false });
  const labels = [...shown.app.querySelectorAll("#happened .beat-label")].map((label) => label.textContent);
  expect(labels).toContain("Target settled");
  expect(shown.app.querySelector("#happened .story").textContent.includes("target.settled")).toBe(false);
});
