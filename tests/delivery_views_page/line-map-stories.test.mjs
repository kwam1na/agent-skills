// The live view's first facts: how many stories have landed out of the total, what landed last, the plan's proof
// under the plan-now sentence, and how long the delivery has run, in the kicker.
process.env.TZ = "UTC";

import { test } from "node:test";

import { readFileSync } from "node:fs";
import path from "node:path";

import { CORPUS, REPLAYED_RUNS, expect, openPage, pageWithRuns, readJson, renderPage } from "./support.mjs";
import { item, journal, minute, planFor, record, recordOnlyDelivery, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";

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

test("a delivery with nothing landed looks ahead to its total instead of counting zero, and a closed one shows its final count", async () => {
  const none = await facts(record({ queue: [item("A", "queued")], planned: [planned("B", "building"), planned("C", "building")] }));
  // Before anything lands, Now draws no facts row: the total reads as large figures, and every piece as an open stop.
  expect(none.querySelector("#now .facts")).toBeNull();
  expect(none.querySelector("#now").classList.contains("is-fresh")).toBe(true);
  expect([...none.querySelectorAll("#now .ready .ready-cell")].map((cell) => cell.textContent)).toEqual(["3pieces to land", "1lane"]);
  expect([...none.querySelectorAll("#now .starting-stop")].map((stop) => stop.getAttribute("aria-label"))).toEqual(
    ["A, Work A, not started", "B, Work B, not started", "C, Work C, not started"]);
  // The closed epic landed all nine: its final count, with nothing left in the queue or the build to count.
  const folder = path.join(CORPUS, "delivery-records-epic");
  const page = renderPage(readJson(path.join(folder, "document.json")), { config: readJson(path.join(CORPUS, "linked-config.json")),
    runsCommands: readJson(path.join(folder, "runs-commands.json")) });
  const [closed] = await openPage(page, { now: NOW, store: false });
  expect(closed.app.querySelector("#now .stories .fact-value").textContent).toBe("9 of 9 landed");
  expect(track(closed.app)).toEqual([["is-landed", 100]]);
  expect(closed.app.querySelector("#now .stories .fact-said")).toBeNull();
});

test("only moving work is counted: a stopped piece isn't in the queue, and a planned story not started or settled isn't being built", async () => {
  const document = underway();
  document.state.queue.push({ ...item("G", "stopped"), order: 5 });
  document.state.planned.push(planned("H", "planned"), planned("I", "settled"));
  const app = await facts(document);
  // A settled planned story has landed; the stopped piece and the story not started are in the total and nowhere else.
  expect(app.querySelector("#now .stories .fact-value").textContent).toBe("3 of 9 landed");
  expect(track(app)).toEqual([["is-landed", 33], ["is-queued", 22], ["is-building", 22]]);
  expect(app.querySelector("#now .stories .fact-said").textContent).toBe("2 in the queue · 2 being built");
});

test("a delivery closed partway counts what the close tallied, with nothing in the queue or the build", async () => {
  const document = record({ queue: [item("A", "merged"), item("B", "checking"), item("C", "queued"), item("D", "stopped")],
    planned: [planned("E", "building")],
    journal: journal([[10, "approve", "A"], [11, "candidate.handed", "A"], [12, "candidate.merged", "A"], [40, "delivery.closed", "A"]]) });
  const plan = planFor(["A", "B", "C", "D", "E", "F"].map((subject) => unit(subject)), { finish: [] });
  const app = await facts(document, plan);
  expect(app.querySelector("#now .now-count").textContent).toBe("Closed with 1 of 6 pieces landed");
  expect(app.querySelector("#now .stories .fact-value").textContent).toBe("1 of 6 landed");
  expect(app.querySelector("#now .stories .fact-said")).toBeNull();
  expect(track(app)).toEqual([["is-landed", 17]]);
});

test("last landed names its ticket and, when the forge knows it, its pull request, each a quiet reference", async () => {
  const app = await facts(underway());
  const last = app.querySelectorAll("#now .facts > .fact")[1];
  expect(last.querySelector(".fact-value").textContent).toBe("Work B");
  const refs = [...last.querySelectorAll(".fact-detail .fact-ref")];
  expect(refs.map((ref) => ref.textContent)).toEqual(["B", "#386"]);
  expect(refs[1].getAttribute("href")).toBe("https://github.com/acme/shop/pull/386");
  expect([refs[1].getAttribute("aria-label"), refs[1].getAttribute("rel"), refs[1].getAttribute("target")])
    .toEqual(["Open pull request acme/shop #386", "noopener noreferrer", "_blank"]);
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
  expect(now.querySelector(".plan-now-plan").textContent).toBe("4 of 6 criteria proven · plan changed 1 time");
  expect(now.querySelector(".plan-now-line .plan-now-link").textContent).toBe("4 of 6 criteria proven");
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
  // The facts sit under the journey with space alone, and no rule between them.
  expect(/border/.test(rule(".chapter-now .journey + .facts"))).toBe(false);
  expect(/font:[^;]*var\(--display\)/.test(rule(".fact-count"))).toBe(true);
  // The track's tones are the landed colour, a wash of it and the hairline, so both themes keep it legible.
  expect(/var\(--landed\)/.test(rule(".fact-track .is-landed"))).toBe(true);
  expect(/var\(--landed\)/.test(rule(".fact-track .is-queued"))).toBe(true);
  expect(/var\(--hair\)/.test(rule(".fact-track .is-building"))).toBe(true);
  const phone = stylesheet.slice(stylesheet.indexOf("@media (max-width: 640px)"));
  const stacked = (phone.match(/\.facts\s*\{([^}]*)\}/) || [])[1] || "";
  expect([/grid-template-columns:\s*minmax\(0, 1fr\)/, /grid-template-rows:\s*none/, /row-gap:\s*28px/].map((one) => one.test(stacked))).toEqual([true, true, true]);
  const moving = stylesheet.match(/[^{}]*\{[^}]*\}/g).filter((one) => /\.facts?(-track|-count|-of|-progress|-said)?\b/.test(one.split("{")[0]));
  expect(moving.some((one) => /\.facts\b/.test(one.split("{")[0]))).toBe(true);
  for (const block of moving) {
    expect([block.split("{")[0].trim(), /animation|transition/.test(block)]).toEqual([block.split("{")[0].trim(), false]);
  }
});


test("the kicker ends with how long the delivery has run, a time element that says its start, and a closed one's whole span", async () => {
  const at = async (now) => {
    const [shown] = await openPage(pageWithRuns(underway(), {}, { config: FORGE }), { now, store: false });
    return shown.app.querySelector("#now .kicker time.kicker-time");
  };
  // Pointing at it shows the page's card, never a native tooltip, saying each end in local time with UTC beside it.
  const said = async (page) => {
    const [, pointed] = await openPage(page, { now: NOW, store: false, steps: [{ hover: "#now .kicker time.kicker-time" }] });
    const card = pointed.app.querySelector(".issue-card");
    const ends = [...card.querySelectorAll(".when-end")].map((end) => [...end.children].map((line) => line.textContent));
    const took = [...card.querySelectorAll(".when-took")].map((line) => line.textContent);
    return [card.hidden, ends, pointed.titled, took];
  };
  // The record's first entry is minute 10, 06:10 UTC; the clock reads on, so a later look says a longer run.
  const time = await at(NOW);
  expect(time.textContent).toBe("Started 5 h 50 min ago");
  expect(time.getAttribute("datetime")).toBe(minute(10));
  // Each end as a person says it, in local time; an open run says how long so far.
  const [hidden, lines, titled, running] = await said(pageWithRuns(underway(), {}, { config: FORGE }));
  expect([hidden, lines.length, titled]).toEqual([false, 1, 0]);
  expect(lines[0][0]).toBe("Started");
  expect(lines[0][1]).toMatch(/^(Today|Yesterday|[A-Z][a-z]{2}, [A-Z][a-z]{2} \d+) at \d{1,2}:\d\d [ap]m$/);
  expect(lines[0].length).toBe(2);
  expect(running).toEqual(["5 hours 50 minutes so far"]);
  expect(time.getAttribute("tabindex")).toBe("0");
  // The kicker is that one fact when the record names no tracker item: no state word, nothing before it.
  expect(time.parentNode.textContent).toBe("Started 5 h 50 min ago");
  expect((await at("2026-10-30T06:40:00Z")).textContent).toBe("Started 30 min ago");
  // A whole hour says no minutes; from 48 hours it says days, to the nearest.
  for (const [now, said] of [["2026-10-30T12:10:00Z", "6 h"], ["2026-11-01T06:09:00Z", "47 h 59 min"], ["2026-11-01T06:10:00Z", "2 days"],
    ["2026-11-01T18:10:00Z", "3 days"]]) {
    expect([now, (await at(now)).textContent]).toEqual([now, "Started " + said + " ago"]);
  }
  // A journal that no longer holds its first entry has no honest start, so the kicker says no duration.
  const trimmed = underway();
  trimmed.journal = trimmed.journal.slice(1);
  const [cut] = await openPage(pageWithRuns(trimmed, {}, { config: FORGE }), { now: NOW, store: false });
  expect(cut.app.querySelector("#now .kicker time")).toBeNull();
  // A closed delivery says the span from its first entry to its close, with both ends on hover.
  const folder = path.join(CORPUS, "delivery-records-epic");
  const page = renderPage(readJson(path.join(folder, "document.json")), { config: readJson(path.join(CORPUS, "linked-config.json")),
    runsCommands: readJson(path.join(folder, "runs-commands.json")) });
  const [closed] = await openPage(page, { now: NOW, store: false });
  const span = closed.app.querySelector("#now .kicker time.kicker-time");
  expect(span.textContent).toBe("Closed after 1 h 34 min");
  const [shut, ends, , took] = await said(page);
  expect([shut, ends.map((end) => end[0]), took]).toEqual([false, ["Started", "Closed"], ["an hour 34 minutes"]]);
  expect(ends.every((end) => end.length === 2 && / at \d{1,2}:\d\d [ap]m$/.test(end[1]))).toBe(true);
});
