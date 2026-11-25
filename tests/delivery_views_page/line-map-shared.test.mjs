// A shared delivery's own chapters: Seats (who writes it, who holds each scope, how fresh each peer is, and a queue
// moving between seats) and Feedback (a thread on each section it is on). A record nobody shares draws neither.
process.env.TZ = "UTC";

import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, expect, openPage, readJson, renderPage } from "./support.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = readJson(path.join(CORPUS, "linked-config.json"));
const PAGES = new Map();

function corpusPage(name, change = (document) => document) {
  const key = name + change.toString();
  if (!PAGES.has(key)) {
    const folder = path.join(CORPUS, name);
    PAGES.set(key, renderPage(change(readJson(path.join(folder, "document.json"))), { config: CONFIG,
      runsCommands: readJson(path.join(folder, "runs-commands.json")), plan: readJson(path.join(folder, "plan.json")) }));
  }
  return PAGES.get(key);
}
async function chapter(name, id, change) {
  const [, shown] = await openPage(corpusPage(name, change), { now: NOW, steps: [{ click: `.rail a[href="#${id}"]` }] });
  return shown.app.querySelector(`#${id}`);
}
const rows = (node, selector) => [...node.querySelectorAll(selector)].map((row) => row.textContent);

test("a record nobody shares has no Seats or Feedback chapter", async () => {
  const [first] = await openPage(corpusPage("plan-review"), { now: NOW });
  expect([first.app.querySelector("#seats"), first.app.querySelector("#feedback"),
    first.app.querySelector('.rail a[href="#seats"]'), first.app.querySelector('.rail a[href="#feedback"]')]).toEqual([null, null, null, null]);
});

test("Seats lists who writes the record, who holds each scope, and how fresh and whether held each peer is", async () => {
  const seats = await chapter("plan-shared", "seats");
  expect(seats.querySelector(".title").textContent).toBe("2 seats write this delivery.");
  expect(rows(seats, ".seat-row")).toEqual(["s-aaaaaaaaaaaaAmaendorses the plan", "s-bbbbbbbbbbbbBen <b>B</b>endorses the plan"]);
  // A section passed to Ben is Ben's; every scope not passed on is the first seat's.
  expect(rows(seats, ".scope-row")).toEqual(["deliveryAma", "queueAma", "lane:lane-1Ama",
    "plan:### SHOP-92. Address book exportBen <b>B</b>"]);
  expect(rows(seats, ".peer-row")).toEqual(["Ben <b>B</b> held: generation differs"]);
  const colead = await chapter("colead", "seats");
  expect(colead.querySelector(".title").textContent).toBe("3 seats write this delivery, and its queue is moving to Ben.");
  expect(rows(colead, ".scope-row")).toEqual(["deliveryAma", "queueBenqueue moving to Ben", "lane:lane-1Ama", "lane:lane-2Ben"]);
  expect(rows(colead, ".seat-row").at(-1)).toBe("s-ccccccccccccCywrites, doesn't endorse");
  expect(rows(colead, ".peer-row")).toEqual(["Ben split", "Cy as of 4m"]);
});

test("once the queue's new holder writes an entry that has seen the handover, the queue is no longer moving", async () => {
  const taken = (document) => {
    const last = document.journal.at(-1);
    document.journal.push({ id: last.id + 1, at: last.at, kind: "plan.seen", lane: null, subject: null, data: { commit: "c".repeat(40) },
      origin: { seat: "s-bbbbbbbbbbbb", seq: 3, seen: { "s-aaaaaaaaaaaa": last.origin.seq } } });
    return document;
  };
  const seats = await chapter("colead", "seats", taken);
  expect(seats.querySelector(".title").textContent).toBe("3 seats write this delivery.");
  expect(rows(seats, ".scope-row")[1]).toBe("queueBen");
});

test("a scope passed on by a seat that doesn't hold it stays with its holder", async () => {
  const usurped = (document) => {
    const last = document.journal.at(-1);
    document.journal.push({ id: last.id + 1, at: last.at, kind: "scope.transferred", lane: null, subject: null,
      data: { scope: "delivery", from: "s-bbbbbbbbbbbb", to: "s-bbbbbbbbbbbb" }, origin: { seat: "s-bbbbbbbbbbbb", seq: 9, seen: {} } });
    return document;
  };
  expect(rows(await chapter("plan-shared", "seats", usurped), ".scope-row")[0]).toBe("deliveryAma");
});

test("Feedback threads each section's feedback: open, blocking, and settled by its section's holder", async () => {
  const feedback = await chapter("plan-shared", "feedback");
  expect(feedback.querySelector(".title").textContent).toBe("2 open, 1 blocking · 3 raised on 3 sections.");
  expect(rows(feedback, ".feedback-anchor")).toEqual(["## Decisions", "### SHOP-91. Saved addresses", "### SHOP-92. Address book export"]);
  expect([...feedback.querySelectorAll(".feedback-row")].map((row) => [row.className, row.querySelector(".feedback-state").textContent])).toEqual([
    ["feedback-row is-blocking", "Open, blocking"], ["feedback-row", "Open"],
    ["feedback-row is-settled", "Accepted by Ben <b>B</b>: CSV it is <img src=x onerror=alert(1)>."]]);
  // A record shared but with no feedback raised has no Feedback chapter.
  const [first] = await openPage(corpusPage("colead"), { now: NOW });
  expect(first.app.querySelector("#feedback")).toBe(null);
});

test("markup in labels and feedback is drawn as text, never as elements", async () => {
  for (const id of ["seats", "feedback", "plan"]) {
    const node = await chapter("plan-shared", id);
    // The page's own emphasis may be bold; none of its elements is one the record's "<b>B</b>" made.
    expect([id, node.querySelectorAll("script, img").length, [...node.querySelectorAll("b")].filter((bold) => bold.textContent === "B").length])
      .toEqual([id, 0, 0]);
  }
  expect((await chapter("plan-shared", "feedback")).textContent).toContain("Per account <script>alert(1)</script>, or per device?");
});

test("a planning record's plan chapter keeps the preview wording and says no form of approve", async () => {
  const words = async (name) => {
    const plan = await chapter(name, "plan");
    const own = plan.querySelector(".plan-approval").textContent;
    const copy = plan.cloneNode(true);
    for (const node of copy.querySelectorAll('.plan-fold[data-fold="approvals"] > summary, .plan-approvals > li')) node.remove();
    const line = copy.querySelector(".plan-approval");
    line.textContent = line.textContent.replace(/ · not approved by a person$/, "");
    return { own, rest: copy.textContent };
  };
  const partial = await words("plan-shared");
  expect(partial.own).toBe("Agent review only · 2 review rounds · every lens aligned · endorsed by Ama · stale endorsement by Ben <b>B</b> · not approved by a person");
  assert.doesNotMatch(partial.rest, /approv/i);
  const endorsed = await words("colead");
  expect(endorsed.own).toBe("Endorsed by Ama and Ben · 3 review rounds · every lens aligned");
  assert.doesNotMatch(endorsed.rest, /approv/i);
});
