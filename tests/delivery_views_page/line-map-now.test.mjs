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
    // The marks' labels carry the holder after a middle dot, as the canvas draws them.
    const expected = { ...fixture.expected, marks: fixture.expected.marks.map(([label, state, who]) => [who ? `${label} · ${who}` : label, state, who]) };
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
  expect([shown.fact, shown.factStop]).toEqual(["No update for 3 hours, so this page may be out of date.", true]);
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
