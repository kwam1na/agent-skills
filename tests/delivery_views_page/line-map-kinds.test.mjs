// Journal kinds the line map names. A base re-run that was stopped, not failed, reads as stopped in the piece's merge
// check, never as a verdict, and the feed draws it. A check carried over a base move reads as carried.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };

test("a stopped base rerun is drawn in the feed and reads as stopped in the piece's merge check", async () => {
  const summary = "Base re-run on main at aaaaaaaaaa after 4s was stopped before its verdict: stop exited 143";
  const document = record({
    queue: [item("A", "queued")],
    journal: journal([[10, "candidate.handed", "A"], [11, "check.started", "A"], [12, "check.failed", "A"],
      [13, "base.rerun.started", "A"], [14, "base.rerun.stopped", "A", { summary }]]),
  });
  const [shown, card] = await openPage(pageWithRuns(document, {}, { config: CONFIG }),
    { now: NOW, store: false, steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  // The timeline draws the entry; what it says is the story chapter's, so only the merge check's words are pinned here.
  expect(shown.app.querySelector(`#happened .feed time[datetime="${document.journal.at(-1).at}"]`) !== null).toBe(true);
  const history = card.app.querySelector("#detail .mh");
  expect(history.querySelector("summary .mh-sub").textContent).toMatch(/^ · Rerun stopped /);
  expect([...history.querySelectorAll("li span")].map((row) => row.textContent).at(-1)).toBe(summary);
});

test("a check carried over a base move is drawn in the feed and reads as carried in the piece's merge check", async () => {
  const summary = "Carried lib to main at bbbbbbbbbb: 1 base commit changed nothing lib reads or A changes";
  const document = record({
    queue: [item("A", "checking")],
    journal: journal([[10, "candidate.handed", "A"], [11, "check.started", "A"], [12, "check.finished", "A"],
      [13, "candidate.stopped", "A"], [14, "check.started", "A"], [15, "check.carried", "A", { summary }]]),
  });
  const [shown, card] = await openPage(pageWithRuns(document, {}, { config: CONFIG }),
    { now: NOW, store: false, steps: [{ click: '.stop-btn[data-subject="A"]' }] });
  // The timeline draws the entry; what it says is the story chapter's, so only the merge check's words are pinned here.
  expect(shown.app.querySelector(`#happened .feed time[datetime="${document.journal.at(-1).at}"]`) !== null).toBe(true);
  const history = card.app.querySelector("#detail .mh");
  expect(history.querySelector("summary .mh-sub").textContent).toMatch(/^ · Check carried /);
  expect([...history.querySelectorAll("li span")].map((row) => row.textContent).at(-1)).toBe(summary);
});
