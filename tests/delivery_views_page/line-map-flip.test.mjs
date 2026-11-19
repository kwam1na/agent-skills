// The line map's numbers flip when a live redraw changes them: each changed digit turns over from the old one, lined
// up from the right, in a layer over the words, which stay one plain text of the new value so the line never moves. A
// first draw, an unchanged number, words of a new shape, a reader who asked for less motion and a browser without
// highlights get plain words.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, renderPage } from "./support.mjs";
import { finishLine, item, journal, planFor, record, unit } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const SUBJECTS = Array.from({ length: 12 }, (_, index) => `P${index + 1}`);
const landed = (subject, at) => [[at, "approve", subject], [at + 1, "candidate.handed", subject], [at + 2, "candidate.merged", subject]];

/** Twelve pieces in one lane with the first `count` landed, and one finish line the record marks met once `met`. */
function delivery(count, met = false) {
  return record({
    queue: SUBJECTS.map((subject, index) => item(subject, index < count ? "merged" : "queued")),
    journal: journal(SUBJECTS.slice(0, count).flatMap((subject, index) => landed(subject, 10 + index * 5))),
    finish: [{ id: "all", text: "Every piece is proven", met }],
  });
}
const PLAN = planFor(SUBJECTS.map((subject) => unit(subject)), { finish: [finishLine("all", { criteria: "all", proofs: [] })] });
const PAGE = pageWithRuns(delivery(9), {}, { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS }, plan: PLAN });

const open = (scenario) => openPage(PAGE, { now: NOW, store: {}, motion: {}, ...scenario });
/** Each turning digit's layer under a node, as [the digit it turns from, the digit it turns to]. */
const flips = (node) => [...node.querySelectorAll(".flip")].map((digit) => [digit.getAttribute("data-from"), digit.getAttribute("data-to")]);
/** The places that count, each with its words and its flipping digits. */
function counts(app) {
  const places = {
    now: app.querySelector("#now .now-count span"),
    stories: app.querySelector("#now .fact-count span"),
    queued: app.querySelector("#now .fact-said"),
    plan: app.querySelector("#now .plan-now-link"),
    lane: app.querySelector("#line .name span"),
    left: app.querySelector("#left .left-number span"),
    say: app.querySelector("#left .say"),
    meter: app.querySelector("#left .pm-count"),
    title: app.querySelector("#plan .plan-title span"),
  };
  // The words are the node's one text, before any layer: the layers hold no text of their own.
  return Object.fromEntries(Object.entries(places).map(([name, node]) => [name, { words: node.firstChild.data, flips: flips(node) }]));
}
/** The merge drawing's counts, each as its words and, while it flips, the old and new characters its layers draw. */
function merge(app) {
  const svg = app.querySelector("#line .line-merge");
  const shown = (text) => [...text.querySelectorAll("tspan")].filter((span) => !span.getAttribute("class")).map((span) => span.textContent).join("");
  return {
    label: svg.getAttribute("aria-label"),
    counts: [...svg.querySelectorAll("text.mg-count:not(.flip-in):not(.flip-out)")].map((text) => text.textContent),
    out: [...svg.querySelectorAll("text.flip-out")].map((text) => [text.textContent, shown(text)]),
    in: [...svg.querySelectorAll("text.flip-in")].map((text) => [text.textContent, shown(text)]),
    held: [...svg.querySelectorAll("tspan.flip-gap")].map((span) => span.textContent),
  };
}

test("a live update flips each changed digit of every count, from the right, and leaves unchanged digits as text", async () => {
  const [, first, second] = await open({ steps: [{ push: delivery(10, true) }, { push: delivery(11, true) }] });
  // While a digit turns, the words hide it; the rest of the words show as they are.
  expect(first.held).toEqual(["1", "0", "1", "0", "1", "0", "2", "1", "0", "2", "1", "0", "1", "0", "1", "0"]);
  // Nine to ten: the units digit turns over from 9, and the tens digit flips in from blank.
  expect(counts(first.app)).toEqual({
    now: { words: "10 of 12", flips: [["", "1"], ["9", "0"]] },
    stories: { words: "10", flips: [["", "1"], ["9", "0"]] },
    queued: { words: "2 in the queue", flips: [["3", "2"]] },
    plan: { words: "10 of 12 criteria proven", flips: [["", "1"], ["9", "0"]] },
    lane: { words: "10 of 12 landed", flips: [["", "1"], ["9", "0"]] },
    left: { words: "2", flips: [["3", "2"]] },
    say: { words: "10 of 12 pieces have landed.", flips: [["", "1"], ["9", "0"]] },
    meter: { words: "10 of 12 criteria proven", flips: [["", "1"], ["9", "0"]] },
    title: { words: "10 of 12 criteria proven.", flips: [["", "1"], ["9", "0"]] },
  });
  expect(merge(first.app)).toEqual({
    label: "Every lane merges into one main line: 10 of 12 pieces landed, 1 of 1 finish lines met",
    counts: ["10/12 landed", "1/1 met"],
    out: [["9/12 landed", "9"], ["0/1 met", "0"]],
    in: [["10/12 landed", "10"], ["1/1 met", "1"]],
    held: ["1", "0", "1"],
  });
  // Ten to eleven, mid-turn: the redraw took the turning words off the page, so only the new turns are held.
  expect(second.held).toEqual(["1", "1", "1", "1", "1", "1", "1", "1", "1"]);
  // The tens digit stays as it was, and the met count, unchanged, does not move.
  expect(counts(second.app).now).toEqual({ words: "11 of 12", flips: [["0", "1"]] });
  expect(counts(second.app).left).toEqual({ words: "1", flips: [["2", "1"]] });
  expect(merge(second.app)).toEqual({
    label: "Every lane merges into one main line: 11 of 12 pieces landed, 1 of 1 finish lines met",
    counts: ["11/12 landed", "1/1 met"],
    out: [["10/12 landed", "0"]],
    in: [["11/12 landed", "1"]],
    held: ["1"],
  });
  // The words are the new value throughout: both turning digits are the stylesheet's to draw, with no text of their own.
  expect(second.app.querySelector("#now .now-count").textContent).toBe("11 of 12 pieces have landed");
  expect(second.text).not.toContain("10 of 12");
  for (const digit of second.app.querySelectorAll(".flip")) {
    expect(digit.getAttribute("aria-hidden")).toBe("true");
    expect(digit.textContent).toBe("");
    expect(digit.parentNode.classList.contains("flipping")).toBe(true);
  }
});

test("once the new digits land the words show their own again, and the layer goes; another motion's end does not end it", async () => {
  const end = (name) => ({ run: (window) => {
    for (const digit of window.document.querySelectorAll(".flip")) {
      const event = new window.Event("animationend", { bubbles: true });
      Object.defineProperty(event, "animationName", { value: name });
      digit.dispatchEvent(event);
    }
  } });
  const [, turning, outDone, landed] = await open({ steps: [{ push: delivery(10, true) }, end("flip-out"), end("flip-in")] });
  expect(outDone.app.querySelectorAll(".flip")).toHaveLength(turning.app.querySelectorAll(".flip").length);
  expect(outDone.held).toEqual(turning.held);
  expect(landed.app.querySelectorAll(".flip, .flipping")).toHaveLength(0);
  expect(landed.held).toEqual([]);
  expect(Object.values(counts(landed.app)).map((place) => place.words))
    .toEqual(["10 of 12", "10", "2 in the queue", "10 of 12 criteria proven", "10 of 12 landed", "2", "10 of 12 pieces have landed.", "10 of 12 criteria proven", "10 of 12 criteria proven."]);
});

test("a redraw that changes no number mid-turn draws the words plain and lets go of the digits it hid", async () => {
  const [, turning, redrawn] = await open({ steps: [{ push: delivery(10, true) }, { tick: true }] });
  expect(turning.held.length).toBeGreaterThan(0);
  // Nothing on the page reads the stale ranges; they go all the same, so the highlight holds only what is turning.
  expect(redrawn.held).toEqual([]);
  expect(redrawn.app.querySelectorAll(".flip, .flipping")).toHaveLength(0);
  expect(counts(redrawn.app).now.words).toBe("10 of 12");
});

test("nothing flips on the first draw, on an update that changes no number, or on the page's own tick", async () => {
  const snapshots = await open({ steps: [{ push: delivery(9) }, { tick: true }] });
  for (const { app } of snapshots) {
    expect(app.querySelectorAll(".flip, .flip-in, .flip-out, .flip-gap")).toHaveLength(0);
    expect(counts(app).now.words).toBe("9 of 12");
    expect(merge(app).counts).toEqual(["9/12 landed", "0/1 met"]);
  }
});

test("for a reader who asked for less motion, or a browser without highlights, a changed number is simply the new one", async () => {
  const [, reduced] = await open({ motion: { reduced: true }, steps: [{ push: delivery(10, true) }] });
  const [, plain] = await open({ motion: undefined, steps: [{ push: delivery(10, true) }] });
  for (const pushed of [reduced, plain]) {
    expect(pushed.app.querySelectorAll(".flip, .flipping")).toHaveLength(0);
    expect(pushed.held).toEqual([]);
    expect(Object.values(counts(pushed.app)).map((place) => place.words))
      .toEqual(["10 of 12", "10", "2 in the queue", "10 of 12 criteria proven", "10 of 12 landed", "2", "10 of 12 pieces have landed.", "10 of 12 criteria proven", "10 of 12 criteria proven."]);
  }
  // The merge's counts are drawing, not words, and need no highlight; they rest under reduced motion only.
  expect(reduced.app.querySelectorAll(".flip-in, .flip-out, .flip-gap")).toHaveLength(0);
  expect(merge(reduced.app).counts).toEqual(["10/12 landed", "1/1 met"]);
});

test("a landing that moves the stop shown to the next piece eases its panel from the panel it replaced, not from empty", async () => {
  const eased = (snapshots) => snapshots.animations.filter((animation) => animation.target === "detail" && animation.options?.id === "run-height");
  // The next piece's panel holds as much as the last one's, so the line below it does not move at all.
  const same = await open({ steps: [{ push: delivery(10) }] });
  expect([same[0], same[1]].map((shot) => shot.app.querySelector("#detail .d1").textContent)).toEqual(["Work P10", "Work P11"]);
  expect(eased(same)).toEqual([]);
  // One that holds more, the first piece's once every piece has landed, eases from the height the replaced panel stood at.
  const last = await open({ steps: [{ push: delivery(12) }] });
  expect(last[1].app.querySelector("#detail .d1").textContent).toBe("Work P1");
  // The page's own arrays, copied out of its window.
  expect(eased(last).map((animation) => Array.from(animation.keyframes, (frame) => frame.height))).toEqual([["180px", "260px"]]);
});

test("as time passes, the kicker's running time and a waiting row's age flip; words that change shape are simply the new ones", async () => {
  const needs = {
    spec: "delivery-needs-you/1", running: true, pass: 4, at: "2026-10-30T11:59:30Z", staleAfter: "2026-10-30T14:00:00Z",
    words: { heading: "Waiting on someone", holding: "Holding up the finish", age: "for {age}" },
    items: [{ holding: false, source: "s", text: "t", clears: "c", kind: "drafts", who: "operator", since: "2026-10-30T11:58:00Z",
      said: { headline: "P10 waits to be marked ready", who: "You" } }],
  };
  const page = renderPage(delivery(9), { config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS }, plan: PLAN, needs });
  // The page's own redraw, a minute on and then an hour on.
  const at = (when) => [{ run: (window) => { window.Date.now = () => Date.parse(when); } }, { tick: true }];
  const [, , minute, , hour] = await openPage(page, { now: NOW, store: {}, motion: {}, steps: [...at("2026-10-30T12:01:00Z"), ...at("2026-10-30T13:01:00Z")] });
  const place = (shot, selector) => { const node = shot.app.querySelector(selector); return { words: node.firstChild.data, flips: flips(node) }; };
  expect(place(minute, ".kicker-time")).toEqual({ words: "Started 5 h 51 min ago", flips: [["0", "1"]] });
  expect(place(minute, ".nr-age")).toEqual({ words: "for 3m", flips: [["2", "3"]] });
  expect(minute.app.querySelector(".kicker-time").getAttribute("tabindex")).toBe("0");
  // An hour on, the age reads in hours where it read in minutes: a new shape, not a turn of the same number.
  expect(place(hour, ".nr-age").flips).toEqual([]);
  expect(place(hour, ".kicker-time")).toEqual({ words: "Started 6 h 51 min ago", flips: [["5", "6"]] });
});
