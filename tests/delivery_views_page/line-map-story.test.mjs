// "What happened" draws the story the narrator told, and words nothing itself. Each story here is the narrator's own,
// for a named journal under tests/fixtures/narrator/, kept byte for byte beside it (tests.test_delivery_views_render
// regenerates each and fails when one differs).
process.env.TZ = "UTC";

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { REPLAYED_RUNS, ROOT, expect, openPage, pageWithRuns, readJson } from "./support.mjs";
import { item, record } from "./plan-fixtures.mjs";

const NOW = "2026-11-16T12:00:00Z";
const CONFIG = {
  spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^[A-Z]$" },
  forges: [{ name: "GitHub", repository: "acme/shop", pullUrl: "https://github.com/{repository}/pull/{number}",
    commitUrl: "https://github.com/{repository}/commit/{sha}" }],
};
const ALTITUDES = { headline: "headline", story: "story", everything: "everything" };

/** A record holding a narrator journal and its pieces, with the story the narrator told of it. */
function storied(name) {
  const folder = path.join(ROOT, "tests", "fixtures", "narrator", name);
  const journal = readFileSync(path.join(folder, "journal.jsonl"), "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const state = existsSync(path.join(folder, "state.json")) ? readJson(path.join(folder, "state.json")) : { queue: [] };
  const document = record({ queue: state.queue.map((piece) => item(piece.subject, piece.stage)), journal });
  document.story = readJson(path.join(ROOT, "tests", "fixtures", "delivery-views", "story", `${name}.json`));
  return document;
}
// Each record renders once: a render starts a process, and most rows open the same few records.
const rendered = new Map();
function pageOf(document) {
  const key = JSON.stringify(document);
  if (!rendered.has(key)) rendered.set(key, pageWithRuns(document, {}, { config: CONFIG }));
  return rendered.get(key);
}
function open(document, scenario = {}) {
  return openPage(pageOf(document), { now: NOW, store: false, ...scenario });
}
const drawn = (snapshot) => [...snapshot.app.querySelectorAll("#happened [data-beat]")].map((row) => row.getAttribute("data-beat"));
const held = (story, altitude) => story.beats.filter((beat) => beat.altitudes.includes(altitude)).map((beat) => beat.id);

test("at each depth the chapter draws exactly the beats the story holds for it, and a setback once at the headline", async () => {
  for (const name of ["thread-at-story", "standing-break", "bookkeeping-run", "batch-of-three", "break-stops-a-piece"]) {
    const document = storied(name);
    const story = document.story;
    const snapshots = await open(document, { steps: Object.keys(ALTITUDES).map((depth) => ({ click: `#happened [data-depth="${depth}"]` })) });
    Object.keys(ALTITUDES).forEach((depth, index) => {
      const shown = snapshots[index + 1];
      expect([name, drawn(shown).sort()]).toEqual([name, held(story, depth).sort()]);
      const sheets = [...shown.app.querySelectorAll("#happened .setback")].map((row) => row.getAttribute("data-thread"));
      expect([name, depth, sheets]).toEqual([name, depth, depth === "headline" ? story.threads.map((thread) => thread.id) : []]);
    });
  }
});

test("at the story altitude a setback's entries keep their places, each marked as the thread's", async () => {
  const document = storied("thread-at-story");
  const [thread] = document.story.threads;
  const [, shown] = await open(document, { steps: [{ click: '#happened [data-depth="story"]' }] });
  const threaded = [...shown.app.querySelectorAll("#happened .beat.threaded")];
  expect(threaded.length).toBe(thread.entries.length);
  expect(threaded.every((row) => row.getAttribute("data-thread") === thread.id)).toBe(true);
  // At the headline the setback is its sheet, and nothing carries the margin line.
  const [, headline] = await open(document, { steps: [{ click: '#happened [data-depth="headline"]' }] });
  expect(headline.app.querySelectorAll("#happened .beat.threaded").length).toBe(0);
});

test("the chapter opens on Story for an open delivery and on Headlines for a closed one, and keeps the reader's choice", async () => {
  const open_ = storied("standing-break");
  const [first] = await open(open_);
  expect(first.app.querySelector('#happened [data-depth="story"]').getAttribute("aria-pressed")).toBe("true");
  const closed = storied("standing-break");
  closed.story = { ...closed.story, state: "closed" };
  const [shut] = await open(closed);
  expect(shut.app.querySelector('#happened [data-depth="headline"]').getAttribute("aria-pressed")).toBe("true");
  const page = pageOf(open_);
  const chosen = await openPage(page, { now: NOW, store: false, rendered: page, steps: [{ click: '#happened [data-depth="everything"]' }] });
  const key = Object.keys(chosen.storage).find((name) => name.endsWith(":story-depth"));
  expect(chosen.storage[key]).toBe("everything");
  const [kept] = await openPage(page, { now: NOW, store: false, rendered: page, stored: { [key]: "everything" } });
  expect(kept.app.querySelector('#happened [data-depth="everything"]').getAttribute("aria-pressed")).toBe("true");
});

test("a beat opens to its kind's explanation and its entries' facts, with commits and tickets linked", async () => {
  const document = storied("break-stops-a-piece");
  const stop = document.journal.find((entry) => entry.kind === "candidate.handed" && entry.answers);
  const beat = document.story.beats.find((each) => each.entries.includes(stop.id) && each.form === "entry");
  const row = `#happened [data-beat="${beat.id}"]`;
  const [closed, opened] = await open(document, { steps: [{ click: '#happened [data-depth="everything"]' }, { click: `${row} summary` }] }).then((snaps) => [snaps[1], snaps[2]]);
  expect(closed.app.querySelector(`${row} details`).hasAttribute("open")).toBe(false);
  const shown = opened.app.querySelector(row);
  expect(shown.querySelector("details").hasAttribute("open")).toBe(true);
  expect(shown.querySelector(".beat-explain").textContent).toBe(beat.explain);
  const facts = Object.fromEntries([...shown.querySelectorAll(".beat-entry > div")].map((pair) => [pair.querySelector("dt").textContent, pair.querySelector("dd")]));
  expect(facts.head.querySelector("a.fact-ref").getAttribute("href")).toBe(`https://github.com/acme/shop/commit/${stop.data.head}`);
  expect(facts.piece.querySelector("[data-ticket]").getAttribute("href")).toBe("https://tracker.example/issue/A");
  expect(facts["change request"].textContent).toBe("#" + stop.data.changeRequest.split("/").pop());
  // An explanation waits to be asked for.
  expect(closed.app.querySelector("#happened .beat-explain:not(details:not([open]) *)")).toBeNull();
});

test("a ticket the story's words name is a link to it, as anywhere else on the page", async () => {
  const document = storied("break-stops-a-piece");
  const [, shown] = await open(document, { steps: [{ click: '#happened [data-depth="story"]' }] });
  const stopped = [...shown.app.querySelectorAll("#happened .beat-said")].find((said) => said.textContent === "A was stopped");
  expect(stopped.querySelector('[data-ticket="A"]').getAttribute("href")).toBe("https://tracker.example/issue/A");
});

test("a kind no page script names draws its label, mark and tab from the story alone", async () => {
  const document = storied("standing-break");
  const story = structuredClone(document.story);
  const beat = story.beats.find((each) => each.form === "entry" && each.altitudes.includes("story"));
  Object.assign(beat, { kind: "x.novel", label: "Weighed and set aside", tone: "decision", words: { head: "The lead set it aside", line: "For now." } });
  document.story = story;
  const [, all, decisions] = await open(document, { steps: [{ click: '#happened [data-depth="story"]' }, { click: '#happened [data-tab="decisions"]' }] });
  const row = (snapshot) => snapshot.app.querySelector(`#happened [data-beat="${beat.id}"]`);
  expect(row(all).querySelector(".beat-label").textContent).toBe("Weighed and set aside");
  expect(row(all).querySelector(".g").className).toBe("g decide");
  expect(row(all).querySelector(".beat-said").textContent).toBe("The lead set it aside");
  expect(drawn(decisions)).toEqual([beat.id]);
  // A chapter with nothing of the tab's in it is left out.
  expect([...decisions.app.querySelectorAll("#happened [data-chapter]")].map((chapter) => chapter.getAttribute("data-chapter"))).toEqual([beat.chapter]);
});

test("a setback is one quiet sheet: why, what it held up, how it ended, and its cost as its only number", async () => {
  const document = storied("break-stops-a-piece");
  const [thread] = document.story.threads;
  const [, shown] = await open(document, { steps: [{ click: '#happened [data-depth="headline"]' }] });
  const sheet = shown.app.querySelector(`#happened .setback[data-thread="${thread.id}"]`);
  const parts = (told) => [told.cause, told.said, told.heldUp, told.ending].filter(Boolean);
  expect([...sheet.querySelectorAll(".setback-part")].map((part) => part.textContent)).toEqual(parts(thread.told));
  expect(thread.told.said && thread.told.heldUp).toBeTruthy();
  expect(sheet.querySelector(".setback-cost b").textContent).toBe("17m");
  expect(sheet.className).toContain("is-closed");
  // With an earlier entry behind it, the sheet opens on what broke and why.
  const caused = storied("thread-at-story");
  const [causedThread] = caused.story.threads;
  const [, why] = await open(caused, { steps: [{ click: '#happened [data-depth="headline"]' }] });
  expect([...why.app.querySelectorAll(`#happened .setback[data-thread="${causedThread.id}"] .setback-part`)].map((part) => part.textContent))
    .toEqual([causedThread.told.cause, causedThread.told.said, causedThread.told.ending]);
  expect(causedThread.told.cause).toBeTruthy();
  const standing = storied("standing-break");
  const [, still] = await open(standing, { steps: [{ click: '#happened [data-depth="headline"]' }] });
  const open_ = still.app.querySelector("#happened .setback");
  expect(open_.className).toContain("is-standing");
  // With nothing earlier behind it, its own line says why it happened.
  const [standingThread] = standing.story.threads;
  expect([...open_.querySelectorAll(".setback-part")].map((part) => part.textContent)).toEqual([standingThread.told.said, "It still stands."]);
  expect(open_.querySelector(".setback-cost b").textContent).toBe("Still standing");
});

test("a setback carried into a later chapter says it still stands there, and one that ended there says until when", async () => {
  const [shown] = await open(storied("standing-break"));
  const carried = [...shown.app.querySelectorAll("#happened .chapter-standing")].map((line) => line.textContent);
  expect(carried).toEqual(["A setback still stands: the main line broke.", "A setback still stands: the main line broke.",
    "A setback still stands: the main line broke."]);
  const document = storied("standing-break");
  const story = structuredClone(document.story);
  story.chapters[1].words.standing = [{ thread: "t1", text: "The main line broke", until: "2026-11-16T09:40:00Z" }];
  document.story = story;
  const [ended] = await open(document);
  expect(ended.app.querySelector(`#happened [data-chapter="c2"] .chapter-standing`).textContent).toBe("The main line broke, until 9:40 am.");
});

test("a run of bookkeeping is the one run that folds, and opened lists each entry in its own words", async () => {
  const document = storied("bookkeeping-run");
  const [run] = document.story.beats.filter((beat) => beat.form === "bookkeeping");
  const row = `#happened [data-beat="${run.id}"]`;
  const [, , opened] = await open(document, { steps: [{ click: '#happened [data-depth="everything"]' }, { click: `${row} summary` }] });
  expect([...opened.app.querySelectorAll(`${row} .beat-items li`)].map((line) => line.textContent)).toEqual(
    run.items.map((each) => each.words.head + (each.words.line ? " " + each.words.line : "")));
  // Its facts are headed by each entry's own words, not the run's.
  const heads = [...opened.app.querySelectorAll(`${row} .beat-entry-head`)].map((head) => head.textContent);
  expect(heads.length).toBe(run.items.length);
  heads.forEach((head, index) => expect(head.startsWith(run.items[index].words.head + " · ")).toBe(true));
  // Everything else shows its words in full, with nothing to open to read them.
  const lines = [...opened.app.querySelectorAll("#happened .beat:not(.setback) .beat-said")];
  expect(lines.length).toBeGreaterThan(1);
});

test("a write to a live record moves the story on an open page without a reload", async () => {
  const before = storied("standing-break");
  const after = storied("break-stops-a-piece");
  const [first, pushed] = await openPage(pageOf(before), { now: NOW, store: { read: "pending" }, steps: [{ push: after }] });
  expect(first.app.querySelector("#happened .title").textContent).toBe(before.story.digest.words.head);
  expect(pushed.app.querySelector("#happened .title").textContent).toBe(after.story.digest.words.head);
  expect(drawn(pushed).sort()).toEqual(held(after.story, "story").sort());
});

/** The legend's entries as drawn: each one's word and its mark. */
const legendOf = (snapshot) => [...snapshot.app.querySelectorAll("#happened .story-legend > span")]
  .map((entry) => ({ word: entry.textContent, mark: entry.querySelector(".g").outerHTML }));

test("the legend says what each mark on the page means, for exactly the tones the depth in view holds", async () => {
  for (const name of ["standing-break", "bookkeeping-run", "break-stops-a-piece"]) {
    const document = storied(name);
    const story = document.story;
    const snapshots = await open(document, { steps: Object.keys(ALTITUDES).map((depth) => ({ click: `#happened [data-depth="${depth}"]` })) });
    Object.keys(ALTITUDES).forEach((depth, index) => {
      const shown = snapshots[index + 1];
      const present = new Set(story.beats.filter((beat) => beat.altitudes.includes(depth)).map((beat) => beat.tone));
      const legend = legendOf(shown);
      expect([name, depth, legend.map((entry) => entry.word)]).toEqual([name, depth,
        story.tones.filter((tone) => present.has(tone.tone)).map((tone) => tone.word)]);
      // Each mark is the one its beats carry, drawn by the same code.
      story.tones.filter((tone) => present.has(tone.tone)).forEach((tone) => {
        const beat = story.beats.find((each) => each.tone === tone.tone && each.altitudes.includes(depth));
        const mark = shown.app.querySelector(`#happened [data-beat="${beat.id}"] > .g`).outerHTML;
        expect([name, depth, tone.tone, legend.find((entry) => entry.word === tone.word).mark]).toEqual([name, depth, tone.tone, mark]);
      });
    });
  }
  // One quiet row between the digest line and the switches.
  const [shown] = await open(storied("standing-break"));
  const legend = shown.app.querySelector("#happened .story-legend");
  expect([legend.previousElementSibling.className, legend.nextElementSibling.className]).toEqual(["say", "story-bar"]);
});

test("a tone no page script names gets its mark and its word in the legend from the story alone", async () => {
  const document = storied("standing-break");
  const story = structuredClone(document.story);
  const beat = story.beats.find((each) => each.form === "entry" && each.altitudes.includes("headline") && each.tone === "landed");
  beat.tone = "x.calm";
  story.tones.push({ tone: "x.calm", word: "Calm" });
  document.story = story;
  const [, headline] = await open(document, { steps: [{ click: '#happened [data-depth="headline"]' }] });
  const calm = legendOf(headline).find((entry) => entry.word === "Calm");
  expect(calm.mark).toBe(headline.app.querySelector(`#happened [data-beat="${beat.id}"] > .g`).outerHTML);
});

test("a story told before it carried its tones' words draws no legend, and nothing in its place", async () => {
  const document = storied("standing-break");
  const story = structuredClone(document.story);
  delete story.tones;
  story.digest.words.line = "";
  document.story = story;
  const [shown] = await open(document);
  const happened = shown.app.querySelector("#happened");
  expect(happened.querySelector(".story-legend")).toBeNull();
  expect(happened.textContent.includes("null")).toBe(false);
});
