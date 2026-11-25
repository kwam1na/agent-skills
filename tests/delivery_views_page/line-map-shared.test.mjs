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
  // Di asked and isn't admitted: listed as waiting, and not counted among the seats that write.
  expect(colead.querySelector(".title").textContent).toBe("3 seats write this delivery, and its queue is moving to Ben.");
  expect(rows(colead, ".seat-row").at(-1)).toBe("s-ddddddddddddDiwaiting to be admitted");
  expect(rows(colead, ".scope-row")).toEqual(["deliveryAma", "queueBenqueue moving to Ben", "lane:lane-1Ama", "lane:lane-2Ben"]);
  // Cy's own relabel, in the journal, wins over the label the record's seats were admitted with.
  expect(rows(colead, ".seat-row").at(-2)).toBe("s-ccccccccccccCyruswrites, doesn't endorse");
  expect(rows(colead, ".peer-row")).toEqual(["Ben held: its updates don't fit this record", "Cyrus as of 4m"]);
  // A split is the record's own, never a peer's: said once, at the chapter's head; a record that hasn't split says nothing.
  expect(colead.querySelector(".seats-split").textContent).toBe("This checkout's seat split from its chain: it writes nothing more here.");
  expect(seats.querySelector(".seats-split")).toBe(null);
});

test("another seat's entry that has seen the handover leaves the queue moving: only its new holder's ends it", async () => {
  const third = (document) => {
    const last = document.journal.at(-1);
    document.journal.push({ id: last.id + 1, at: last.at, kind: "plan.seen", lane: null, subject: null, data: { commit: "c".repeat(40) },
      origin: { seat: "s-cccccccccccc", seq: 3, seen: { "s-aaaaaaaaaaaa": 99 } } });
    return document;
  };
  expect((await chapter("colead", "seats", third)).querySelector(".title").textContent)
    .toBe("3 seats write this delivery, and its queue is moving to Ben.");
});

test("once the queue's new holder writes an entry that has seen the handover, the queue is no longer moving", async () => {
  const taken = (document) => {
    const last = document.journal.at(-1);
    const handover = document.journal.filter((entry) => entry.kind === "scope.transferred" && entry.data.scope === "queue").at(-1);
    document.journal.push({ id: last.id + 1, at: last.at, kind: "plan.seen", lane: null, subject: null, data: { commit: "c".repeat(40) },
      origin: { seat: "s-bbbbbbbbbbbb", seq: 3, seen: { "s-aaaaaaaaaaaa": handover.origin.seq } } });
    return document;
  };
  const seats = await chapter("colead", "seats", taken);
  expect(seats.querySelector(".title").textContent).toBe("3 seats write this delivery.");
  expect(rows(seats, ".scope-row")[1]).toBe("queueBen");
});

const transfer = (document, seat, data) => {
  const last = document.journal.at(-1);
  document.journal.push({ id: last.id + 1, at: last.at, kind: "scope.transferred", lane: null, subject: null, data,
    origin: { seat, seq: 90 + document.journal.length, seen: {} } });
};
const AMA = "s-aaaaaaaaaaaa", BEN = "s-bbbbbbbbbbbb";

test("a scope passed on by a seat that doesn't hold it stays with its holder, whatever it names as from", async () => {
  // Ben, holding neither, names himself and then Ama as the scope's holder: neither transfer counts.
  const usurped = (document) => {
    transfer(document, BEN, { scope: "delivery", from: BEN, to: BEN });
    transfer(document, BEN, { scope: "queue", from: AMA, to: BEN });
    // Ama, the holder, naming another seat as from: no transfer either.
    transfer(document, AMA, { scope: "lane:lane-1", from: BEN, to: BEN });
    return document;
  };
  expect(rows(await chapter("plan-shared", "seats", usurped), ".scope-row").slice(0, 3)).toEqual(["deliveryAma", "queueAma", "lane:lane-1Ama"]);
});

test("a scope's later holder passes it on, and every section nobody passed on follows the delivery's holder", async () => {
  const handed = (document) => {
    transfer(document, AMA, { scope: "delivery", from: AMA, to: BEN });
    // Ben holds every section nobody passed on now, so Ben's handing one to Ama counts.
    transfer(document, BEN, { scope: "plan:## Decisions", from: BEN, to: AMA });
    return document;
  };
  const seats = await chapter("plan-shared", "seats", handed);
  expect(rows(seats, ".scope-row")).toEqual(["deliveryBen <b>B</b>", "queueAma", "lane:lane-1Ama",
    "plan:### SHOP-92. Address book exportBen <b>B</b>", "plan:## DecisionsAma"]);
  expect(seats.querySelector(".seats-note").textContent).toBe("Every other plan section is Ben <b>B</b>'s, as the delivery's holder.");
});

test("a scope handed on once can be handed on again by its new holder", async () => {
  // Ben holds lane-2, passed to him by Ama; passing it to Cy counts, as Ben is its holder now.
  const again = (document) => {
    transfer(document, BEN, { scope: "lane:lane-2", from: BEN, to: "s-cccccccccccc" });
    return document;
  };
  expect(rows(await chapter("colead", "seats", again), ".scope-row").at(-1)).toBe("lane:lane-2Cyrus");
});

test("a record that stops being shared hides its Seats and Feedback chapters and their links", async () => {
  const unshared = structuredClone(readJson(path.join(CORPUS, "plan-shared", "document.json")));
  delete unshared.state.meta.collab;
  // Every chapter on one page, so a chapter is hidden only because it has emptied.
  const snaps = await openPage(corpusPage("plan-shared"), { now: NOW,
    steps: [{ click: ".chapter-tab-button" }, { click: ".chapter-tab-layout" }, { push: unshared }] });
  expect(snaps[2].app.querySelector("#seats").hidden).toBe(false);
  const after = snaps.at(-1).app;
  expect(["#seats", "#feedback"].map((id) => after.querySelector(id).hidden)).toEqual([true, true]);
  expect(['.rail a[href="#seats"]', '.rail a[href="#feedback"]'].map((link) => after.querySelector(link).hidden)).toEqual([true, true]);
});

test("Feedback threads each section's feedback: open, blocking, and settled by its section's holder", async () => {
  const feedback = await chapter("plan-shared", "feedback");
  expect(feedback.querySelector(".title").textContent).toBe("2 open, 1 blocking · 4 raised on 3 sections.");
  expect(rows(feedback, ".feedback-anchor")).toEqual(["## Decisions", "### SHOP-91. Saved addresses", "### SHOP-92. Address book export"]);
  // Settled by the section's holder, who need not be who raised it.
  expect([...feedback.querySelectorAll(".feedback-row")].map((row) => [row.className, row.querySelector(".p").textContent,
    row.querySelector(".feedback-state").textContent])).toEqual([
    ["feedback-row is-blocking", "Ben <b>B</b>", "Open, blocking"], ["feedback-row", "Ama", "Open"],
    ["feedback-row is-settled", "Ben <b>B</b>", "Declined by Ama: The cap stays."],
    ["feedback-row is-settled", "Ben <b>B</b>", "Accepted by Ben <b>B</b>: CSV it is <img src=x onerror=alert(1)>."]]);
  // Every item settled, one of them answered: none open.
  const settledAll = (document) => {
    const last = document.journal.at(-1);
    for (const [id, outcome] of [[6, "answered"], [7, "accepted"]]) {
      document.journal.push({ id: document.journal.length + 1, at: last.at, kind: "feedback.resolved", lane: null, subject: null,
        data: { outcome, reason: "Done." }, answers: [id], origin: { seat: AMA, seq: 80 + id, seen: {} } });
    }
    return document;
  };
  const done = await chapter("plan-shared", "feedback", settledAll);
  expect(done.querySelector(".title").textContent).toBe("None open · 4 raised on 3 sections.");
  expect(done.querySelector(".feedback-row .feedback-state").textContent).toBe("Answered by Ama: Done.");
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
