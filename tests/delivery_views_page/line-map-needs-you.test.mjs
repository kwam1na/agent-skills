// What needs someone is one list in the lanes' own marks. A neutral heading and one sentence open it; each row draws
// its story's stop as the line does, with a wait's blocker joined by a hairline, then a headline, a why, a meta line
// that opens with who acts, and how long it has stood. Every word is the supervisor's, said from the table of wait
// sentences; the page lays the words out and says none of its own.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { ROOT, expect, openPage, renderPage } from "./support.mjs";

const config = { spec: "delivery-views-config/1" };
const NOW = "2026-11-17T12:00:00Z";
const lane = (id, state, waitingOn = null) => ({ id, title: id === "lane-1" ? "Waits" : "Renderer", focus: "Which span?", state,
  waitingOn, since: "2026-11-17T10:00:00Z" });

/** APP-1 stopped in lane 1; APP-2, planned in lane 2, waits for it; lane 2 is blocked on a question for the lead. */
function record() {
  return {
    state: {
      meta: { headline: "Needs you", updatedAt: NOW, checking: { now: null, next: [] } },
      lanes: [lane("lane-1", "assigned"), lane("lane-2", "blocked", "lead")],
      queue: [{ order: 1, subject: "APP-1", title: "Stop", lane: "lane-1", stage: "stopped", head: "a".repeat(40), checks: [] }],
      planned: [{ subject: "APP-2", title: "Wait", lane: "lane-2", stage: "building", after: ["APP-1"] }],
    },
    journal: [{ id: 1, at: "2026-11-17T11:00:00Z", kind: "candidate.stopped", lane: "lane-1", subject: "APP-1", data: {} }],
  };
}
/** The list as the supervisor writes it: each item's facts and the words it said them in. */
function needs(over = {}) {
  return {
    spec: "delivery-needs-you/1", running: true, pass: 4, at: "2026-11-17T11:59:30Z", staleAfter: "2026-11-17T12:01:30Z",
    summary: "1 thing needs you and 2 things need the lead, and 1 of them is holding up the finish.",
    words: { heading: "Waiting on someone", holding: "Holding up the finish", age: "for {age}" },
    items: [
      { holding: false, source: "journal entry 1 (candidate.stopped)", text: "APP-1 stopped", clears: "c", kind: "stopped", who: "lead",
        subject: "APP-1", lane: "lane-1", state: "stopped", since: "2026-11-17T11:00:00Z",
        said: { headline: "APP-1 was stopped", why: "1 piece waits on it.", clears: "Clears when APP-1 is put back.", who: "The lead" } },
      { holding: true, source: "lane-2's state", text: "lane-2 is blocked: Which span?", clears: "c", kind: "laneBlocked", who: "lead",
        lane: "lane-2", subject: "APP-2", state: "building", since: "2026-11-17T11:02:00Z",
        blocker: { subject: "APP-1", lane: "lane-1", state: "stopped" },
        said: { headline: "Lane 2 is blocked on APP-2", why: "Which span?", clears: "Clears when Lane 2's state changes.", who: "The lead" } },
      { holding: false, source: "acceptance", text: "drafts", clears: "c", kind: "drafts", who: "operator",
        said: { headline: "APP-1 waits to be marked ready", why: "The merge step refuses drafts.", clears: "Clears when it is marked ready.", who: "You" } },
    ],
    ...over,
  };
}
async function shown(list = needs(), document = record()) {
  const [page] = await openPage(renderPage(document, { config, needs: list }), { now: NOW });
  return page;
}
const rows = (page) => [...page.app.querySelectorAll(".needs-you li")];

test("c1: there is one list, no Needs attention, and a blocked lane's question is in it once", async () => {
  const page = await shown();
  expect(page.app.querySelectorAll(".needs-you").length).toBe(1);
  expect(page.app.querySelector(".attention:not(.close-partway)")).toBeNull();
  expect(page.text).not.toContain("Needs attention");
  expect(page.app.querySelector("#now").textContent.split("Which span?").length - 1).toBe(1);
  // Without the supervisor's list the record's blocked lane and stopped piece draw no list of the page's own.
  const bare = (await openPage(renderPage(record(), { config }), { now: NOW }))[0];
  expect(bare.app.querySelector(".needs-you, .attention")).toBeNull();
});

test("c2: a neutral heading and the list's own sentence open it, with no group headers", async () => {
  const box = (await shown()).app.querySelector(".needs-you");
  expect(box.querySelector(".ah").textContent).toBe("Waiting on someone");
  expect(box.querySelector(".needs-summary").textContent)
    .toBe("1 thing needs you and 2 things need the lead, and 1 of them is holding up the finish.");
  expect(box.querySelector(".needs-group, .needs-kind")).toBeNull();
});

test("c3: a row draws its stop and its blocker's as the line does, then its words and how long it has stood", async () => {
  const page = await shown();
  const blocked = rows(page).find((row) => row.querySelector(".a1").textContent === "Lane 2 is blocked on APP-2");
  const stops = [...blocked.querySelectorAll(".nr-stop")];
  // The story's stop takes its lane's colour and the line's own mark for its state; the blocker's takes its own lane's.
  expect(stops.map((stop) => stop.style.getPropertyValue("--c"))).toEqual(["var(--l2)", "var(--l1)"]);
  const lineMark = (subject) => page.app.querySelector(`:is(.stop-btn, .mg-tick)[data-subject="${subject}"]`).className.replace("stop-btn ", "");
  expect(stops.map((stop) => stop.querySelector(".stop-btn").className)).toEqual([`stop-btn ${lineMark("APP-2")}`, `stop-btn ${lineMark("APP-1")}`]);
  expect(blocked.querySelector(".nr-hair")).not.toBeNull();
  expect(stops.map((stop) => stop.querySelector(".nr-key").textContent)).toEqual(["2", "1"]);
  expect(stops[0].getAttribute("title")).toBe("Renderer · APP-2 · Being built");
  expect(blocked.querySelector(".nr-stroke")).not.toBeNull();
  expect(blocked.querySelector(".a2").textContent).toBe("Which span?");
  expect(blocked.querySelector(".nr-clears").textContent).toBe("Clears when Lane 2's state changes.");
  const age = blocked.querySelector(".nr-age");
  expect(age.textContent).toBe("for 58m");
  expect(age.getAttribute("title")).toBe("11:02 am");
  // A row with no story draws no stop, and one with no time no age.
  const drafts = rows(page).find((row) => row.querySelector(".nr-who").textContent === "You");
  expect(drafts.querySelector(".nr-stop")).toBeNull();
  expect(drafts.querySelector(".nr-age").textContent).toBe("");
});

test("c4: holding rows lead and carry the holding mark; the rest follow quieter; red is only a stopped stop's", async () => {
  const page = await shown();
  const all = rows(page);
  expect(all.map((row) => row.className)).toEqual(["needs-row is-holding", "needs-row is-quiet", "needs-row is-quiet"]);
  expect(all.map((row) => row.querySelector(".nr-holding")?.textContent ?? null)).toEqual(["Holding up the finish", null, null]);
  const marks = [...page.app.querySelectorAll(".needs-you .stop-btn")].map((mark) => mark.className);
  expect(marks.filter((mark) => mark.includes("held"))).toEqual(["stop-btn held", "stop-btn held"]);
  // No rule the list adds takes the held colour: red stays the stopped stop's, which the line's own rule draws.
  const sheet = readFileSync(path.join(ROOT, "skills/delivery-views/assets/line-map.css"), "utf8");
  const own = sheet.split("}").filter((rule) => /\.(needs-|nr-)/.test(rule.split("{")[0]));
  expect(own.length > 0).toBe(true);
  expect(own.filter((rule) => rule.includes("--held"))).toEqual([]);
});

test("c5: the rows show whatever words the list gives, and the page adds none", async () => {
  const marked = needs();
  marked.summary = "⟦summary⟧";
  marked.words = { heading: "⟦heading⟧", holding: "⟦holding⟧", age: "⟦age⟧ {age}" };
  marked.items.forEach((item, n) => { item.said = { headline: `⟦h${n}⟧`, why: `⟦w${n}⟧`, clears: `⟦c${n}⟧`, who: `⟦who${n}⟧` }; });
  const box = (await shown(marked)).app.querySelector(".needs-you");
  // Every piece of text in the box is a marked word, a story's key, or the age the page measures.
  const texts = (node) => [...node.childNodes].flatMap((child) => child.nodeType === 3 ? [child.textContent] : texts(child));
  const pieces = texts(box).filter((piece) => piece.trim());
  expect(pieces.filter((piece) => !/^⟦[a-z0-9]+⟧/.test(piece) && !/^[0-9]$/.test(piece))).toEqual([]);
  expect(pieces).toContain("⟦age⟧ 58m");
  expect(pieces).toContain("⟦holding⟧");
  expect(pieces).toContain("⟦heading⟧");
});

test("c6: each meta line opens with who acts, and the rows the reader acts on lead their group", async () => {
  const list = needs();
  list.items.push({ holding: true, source: "s", text: "t", clears: "c", kind: "drafts", who: "operator",
    said: { headline: "Mark it ready", why: "", clears: "Clears when it is ready.", who: "You" } });
  list.items.push({ holding: false, source: "s", text: "t", clears: "c", kind: "laneAsks", who: "lane-1",
    said: { headline: "Lane 2 waits on Lane 1", why: "", clears: "Clears when it answers.", who: "Lane 1" } });
  const all = rows(await shown(list));
  expect(all.map((row) => row.querySelector(".nr-meta").firstChild.textContent)).toEqual(["You", "The lead", "You", "The lead", "Lane 1"]);
  expect(all.map((row) => row.querySelector(".a1").textContent)).toEqual([
    "Mark it ready", "Lane 2 is blocked on APP-2", "APP-1 waits to be marked ready", "APP-1 was stopped", "Lane 2 waits on Lane 1"]);
});

test("a page that watches its delivery close shows what it left open after that push, with no reload", async () => {
  const open = record();
  const closed = record();
  closed.journal.push({ id: 2, at: "2026-11-17T11:50:00Z", kind: "delivery.closed", lane: null, subject: null, data: {} });
  const [before, after] = await openPage(renderPage(open, { config }), { now: NOW, rendered: renderPage(closed, { config }),
    steps: [{ push: closed }, { wait: 3100 }] }).then((snapshots) => [snapshots[0], snapshots[2]]);
  expect(before.app.querySelector(".left-open")).toBeNull();
  const box = after.app.querySelector("#now .left-open");
  expect(box.querySelector(".ah").textContent).toBe("Left open at close");
  expect([...box.querySelectorAll(".a1")].map((row) => row.textContent)).toEqual(["APP-1 was stopped"]);
});

test("the list is a sheet as wide as the rows, and when something on it waits on the reader it leads Now", async () => {
  const order = (page) => [...page.app.querySelector("#now .inner").children].map((node) => node.classList[0]);
  // A row waits on the operator: the sheet leads, right under the sentence and its marks, and is marked as leading.
  const yours = await shown();
  const sheet = yours.app.querySelector("#now .needs-you");
  expect([sheet.classList.contains("is-sheet"), sheet.classList.contains("is-leading")]).toEqual([true, true]);
  const lead = order(yours);
  expect(lead.indexOf("needs-you")).toBe(lead.indexOf("now-state") + 1);
  expect(rows(yours).map((row) => row.getAttribute("data-who"))).toEqual(["lead", "operator", "lead"]);
  // Nothing on it waits on the reader: still a sheet, but in its place below, not leading.
  const theirs = await shown(needs({ items: needs().items.filter((item) => item.who !== "operator") }));
  const quiet = theirs.app.querySelector("#now .needs-you");
  expect([quiet.classList.contains("is-sheet"), quiet.classList.contains("is-leading")]).toEqual([true, false]);
  const below = order(theirs);
  expect(below.indexOf("needs-you") > below.indexOf("now-state") + 1).toBe(true);
});

test("a list that waits on the reader leads ahead of a running check", async () => {
  const document = record();
  document.state.queue.push({ order: 2, subject: "APP-3", title: "Check", lane: "lane-1", stage: "checking", head: "b".repeat(40), checks: [] });
  document.state.meta.checking = { now: { subject: "APP-3", startedAt: "2026-11-17T11:58:00Z",
    steps: [{ label: "unit tests", state: "running", startedAt: "2026-11-17T11:58:00Z" }] }, next: [] };
  const page = await shown(needs(), document);
  const order = [...page.app.querySelector("#now .inner").children].map((node) => node.classList[0]);
  expect(order.indexOf("needs-you")).toBe(order.indexOf("now-state") + 1);
  expect(order.indexOf("checking")).toBe(order.indexOf("needs-you") + 1);
});
