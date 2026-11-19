// An epic split by codebase is delivered by one delivery per repository, each naming the others as siblings. The state
// wrapper words what each sibling's last kept read shows, and the line map draws it: an "Across repositories" chapter
// with a folded group per sibling, the part-done headline in place of a done one, and the outside children a sibling
// holds drawn in its group rather than under "Elsewhere". A record with no siblings draws as it always did.
import { test } from "node:test";
import { JSDOM } from "jsdom";

import { REPLAYED_RUNS, builtinPage, expect, openPage, pageWithRuns, withAcross } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
// The last read that changed a sibling, and when its latest outcome began.
const READ = "2026-10-30T11:50:00Z";
const ATTEMPT = "2026-10-30T11:55:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^[A-Z]+-[0-9]+$" } };
const LINK = "https://views.example/d/" + "a".repeat(64);

/** A sibling as reconcile leaves it: its last kept read's stories, and its latest read's outcome. */
function sibling(name, stories, outcome = "read", fields = {}) {
  const read = outcome === "never" ? {} : { readAt: READ, stories };
  return { name, link: LINK, addedAt: "2026-10-30T10:00:00Z",
    ...(outcome === "never" ? {} : { lastOutcome: outcome, lastAttemptAt: ATTEMPT }), ...read, ...fields };
}
function stories(...stages) {
  return stages.map((stage, index) => ({ subject: `API-${index + 1}`, title: `Backend ${index + 1}`, stage }));
}
/** This delivery's record: one story, merged or still building, then closed when asked. */
function document(siblings, { landed = true, closed = false, outside } = {}) {
  const steps = landed ? [[10, "candidate.handed", "APP-21"], [11, "candidate.merged", "APP-21"]] : [[10, "lane.assigned", "APP-21", { data: { title: "Login" } }]];
  if (closed) steps.push([20, "delivery.closed", null]);
  const shown = record({ queue: [item("APP-21", landed ? "merged" : "building")], journal: journal(steps), meta: { trackerParent: "APP-10" } });
  if (siblings !== undefined) shown.state.siblings = siblings;
  if (outside !== undefined) shown.state.outside = outside;
  return withAcross(shown);
}
async function open(doc, scenario = {}) {
  return openPage(pageWithRuns(doc, {}, { config: CONFIG }), { now: NOW, store: false, ...scenario });
}
function said(shown) {
  return { title: shown.app.querySelector("#now .now-count").textContent,
    across: [...shown.app.querySelectorAll("#now .say-across")].map((node) => node.textContent) };
}

test("the chapter draws a folded group per sibling: since when each has shown no change, or why it can't be read", async () => {
  const doc = document([sibling("backend", stories("building", "merged")), sibling("payments", stories("review"), "unavailable"),
    sibling("web", [], "never")]);
  const [shown, opened] = await open(doc, { steps: [{ toggle: "#across .across-fold" }] });
  const chapter = shown.app.querySelector("#across");
  expect(chapter.hidden).toBe(false);
  expect(shown.app.querySelector('.rail a[href="#across"]').hidden).toBe(false);
  // After What's left, on the page and on the rail.
  const order = [...shown.app.querySelectorAll(".chapter")].map((node) => node.id);
  expect(order.indexOf("across")).toBe(order.indexOf("left") + 1);
  expect(chapter.querySelector(".title").textContent).toBe("3 other repositories deliver this epic. 2 can't be read now.");
  const fold = chapter.querySelector(".across-fold");
  expect(fold.hasAttribute("open")).toBe(false);
  expect(fold.querySelector("summary").textContent).toBe("backend · payments · web");
  const heads = [...fold.querySelectorAll(".across-head")].map((node) => node.textContent);
  expect(heads).toEqual(["backendNo change since 10 minutes ago", "paymentsCouldn't be read since 5 minutes ago", "webNever read"]);
  const name = fold.querySelector("a.across-name");
  expect(name.getAttribute("href")).toBe(LINK);
  expect(name.getAttribute("rel")).toBe("noopener noreferrer");
  expect([...fold.querySelectorAll(".across-read time")].map((node) => node.getAttribute("datetime"))).toEqual([READ, ATTEMPT]);
  const rows = [...fold.querySelectorAll(".across-group")].map((group) => [...group.querySelectorAll("li")].map((row) => row.textContent));
  expect(rows).toEqual([["API-1Backend 1Being built", "API-2Backend 2Landed"], ["API-1Backend 1In review"], []]);
  expect(fold.querySelector('a.issue-link[data-ticket="API-1"]').getAttribute("href")).toBe("https://tracker.example/issue/API-1");
  expect(opened.app.querySelector("#across .across-fold").hasAttribute("open")).toBe(true);
});

test("an opened fold stays open through a redraw", async () => {
  const doc = document([sibling("backend", stories("building"))]);
  const [, , pushed] = await open(doc, { store: { read: "pending" },
    steps: [{ toggle: "#across .across-fold" }, { push: document([sibling("backend", stories("merged"))]) }] });
  expect(pushed.app.querySelector("#across .across-fold").hasAttribute("open")).toBe(true);
  expect(pushed.app.querySelector("#across .title").textContent).toBe("1 other repository delivers this epic.");
});

test("own stories all landed and a fresh sibling with two open: this repository's part is done", async () => {
  for (const closed of [false, true]) {
    const [shown] = await open(document([sibling("backend", stories("building", "review", "merged"))], { closed }));
    // The delivery still closes: the closed record says part done in place of Delivered.
    expect(said(shown)).toEqual({ title: "This repository's part is done", across: ["backend has 2 open."] });
  }
});

test("every sibling fresh with everything landed: the usual done headline", async () => {
  const [shown] = await open(document([sibling("backend", stories("merged", "settled"))]));
  expect(said(shown)).toEqual({ title: "Every piece has landed", across: [] });
  const [closed] = await open(document([sibling("backend", stories("merged"))], { closed: true }));
  expect(said(closed)).toEqual({ title: "Every piece landed", across: [] });
});

test("a sibling that can't be read now never lets the done headline show", async () => {
  const cases = [
    [sibling("backend", stories("building", "review"), "unavailable"), `backend has 2 open, couldn't be read since ${ATTEMPT}.`],
    [sibling("backend", [], "never"), "backend couldn't be read (never read)."],
    [sibling("backend", stories("merged"), "unavailable"), `backend couldn't be read since ${ATTEMPT}.`],
    [sibling("backend", stories("merged"), "stale"), `backend couldn't be read since ${ATTEMPT}.`],
  ];
  for (const [row, words] of cases) {
    const [shown] = await open(document([row]));
    expect(said(shown)).toEqual({ title: "This repository's part is done", across: [words] });
  }
});

test("before every own story has landed, the headline is today's whatever the siblings say", async () => {
  const [shown] = await open(document([sibling("backend", stories("building"))], { landed: false }));
  expect(said(shown)).toEqual({ title: "0 of 1 pieces have landed", across: [] });
});

test("a sibling of another epic is named under Also linked, without stories, and isn't named in the headline", async () => {
  const [shown] = await open(document([sibling("legacy", stories("building"), "other-epic"), sibling("backend", stories("merged"))]));
  expect(said(shown)).toEqual({ title: "Every piece has landed", across: [] });
  const chapter = shown.app.querySelector("#across");
  // The chapter counts and folds the epic's own groups; another epic's comes after them, outside the fold.
  expect(chapter.querySelector(".title").textContent).toBe("1 other repository delivers this epic.");
  expect(chapter.querySelector(".across-fold summary").textContent).toBe("backend");
  expect([...chapter.querySelectorAll(".across-group .across-name")].map((node) => node.textContent)).toEqual(["backend"]);
  const also = chapter.querySelector(".across-also");
  expect(also.previousElementSibling.classList.contains("across-fold")).toBe(true);
  expect(also.querySelector(".across-also-head").textContent).toBe("Also linked");
  expect(also.querySelector(".across-head").textContent).toBe("legacyDelivers another epic since 5 minutes ago");
  expect(also.querySelector("a.across-name").getAttribute("href")).toBe(LINK);
  expect(also.querySelector("li")).toBeNull();
  // With only another epic's group the chapter still names it, and says no other repository delivers this epic.
  const [alone] = await open(document([sibling("legacy", stories("building"), "other-epic")]));
  expect(alone.app.querySelector("#across").hidden).toBe(false);
  expect(alone.app.querySelector("#across .title").textContent).toBe("No other repository delivers this epic.");
  expect(alone.app.querySelector("#across .across-fold")).toBeNull();
  expect(alone.app.querySelector("#across .across-also .across-head").textContent).toBe("legacyDelivers another epic since 5 minutes ago");
});

test("an outside child a sibling holds is drawn in its group; one no sibling holds stays elsewhere", async () => {
  const outside = [{ subject: "API-1", title: "[Backend] Login API", state: "started", status: "In Progress", marks: { component: ["Backend"] } },
    { subject: "OPS-4", title: "Alerting", state: "backlog", status: "To Do", marks: { component: ["Ops"] } }];
  const [shown] = await open(document([sibling("backend", stories("building"))], { outside }));
  expect([...shown.app.querySelectorAll("#across li")].map((row) => row.textContent)).toEqual(["API-1Backend 1Being built"]);
  const fold = shown.app.querySelector("#parked .elsewhere-fold");
  expect(fold.querySelector("summary").textContent).toBe("Elsewhere in APP-10 · 1 piece another delivery holds");
  expect([...fold.querySelectorAll("li")].map((row) => row.textContent)).toEqual(["OPS-4AlertingTo Do · Ops"]);
  // A sibling of another epic holds nothing here: its child stays elsewhere.
  const [other] = await open(document([sibling("backend", stories("building"), "other-epic")], { outside }));
  expect([...other.app.querySelectorAll("#parked .elsewhere-fold li")].length).toBe(2);
});

test("a record with no siblings has no chapter and no rail link, and a sibling named later makes one", async () => {
  const [shown, , pushed] = await open(document(undefined), { store: { read: "pending" },
    steps: [{ run: () => {} }, { push: document([sibling("backend", stories("building"))]) }] });
  expect(shown.app.querySelector("#across")).toBeNull();
  expect(shown.app.querySelector('.rail a[href="#across"]')).toBeNull();
  expect(shown.app.querySelector("#now .say-across")).toBeNull();
  expect(pushed.app.querySelector("#across").hidden).toBe(false);
  const rail = [...pushed.app.querySelectorAll(".rail a")].map((link) => link.getAttribute("href"));
  expect(rail.indexOf("#across")).toBe(rail.indexOf("#left") + 1);
  // A record whose siblings are all removed hides the chapter again.
  const [, , emptied] = await open(document([sibling("backend", stories("building"))]), { store: { read: "pending" },
    steps: [{ run: () => {} }, { push: document([]) }] });
  expect(emptied.app.querySelector("#across").hidden).toBe(true);
  expect(emptied.app.querySelector('.rail a[href="#across"]').hidden).toBe(true);
});

// orchestrate-work's built-in page prints the same words: the part-done line under the status, then a folded group per
// sibling after Planned, and Elsewhere with the children no sibling holds.
function builtin(doc) {
  // The built-in page reads the record whole, as the state tool shows it.
  doc.state.meta = { baseHead: "b".repeat(40), releasedHead: "b".repeat(40), checking: { now: null, next: [] }, ...doc.state.meta };
  const dom = new JSDOM(builtinPage(doc), { runScripts: "dangerously" });
  const app = dom.window.document.getElementById("app");
  // A page that failed to draw has no sections: a case that finds nothing must still have drawn.
  expect(app.querySelector("h2")).not.toBeNull();
  const found = {
    headings: [...app.querySelectorAll("h2")].map((node) => node.textContent),
    statuses: [...app.querySelectorAll("p.across")].map((node) => node.textContent),
    folds: [...app.querySelectorAll("details")].map((fold) => ({ open: fold.open, summary: fold.querySelector("summary").textContent,
      rows: [...fold.querySelectorAll(":scope > ul > li")].map((row) => row.textContent) })),
  };
  dom.window.close();
  return found;
}

test("the built-in page draws each sibling's group, folded, and says when part of the epic is still open", () => {
  const outside = [{ subject: "API-1", title: "[Backend] Login API", state: "started", status: "In Progress" },
    { subject: "OPS-4", title: "Alerting", state: "backlog", status: "To Do" }];
  const shown = builtin(document([sibling("backend", stories("building", "merged")), sibling("payments", stories("review"), "unavailable"),
    sibling("web", [], "never")], { outside }));
  expect(shown.statuses).toEqual(["This repository's part is done. backend has 1 open. payments has 1 open, couldn't be read since " +
    ATTEMPT + ". web couldn't be read (never read)."]);
  const planned = shown.headings.indexOf("Planned");
  expect(shown.headings.slice(planned + 1, planned + 3)).toEqual(["Across repositories", "Elsewhere in APP-10"]);
  expect(shown.folds.map((fold) => [fold.open, fold.summary])).toEqual([[false, "3 sibling deliveries"], [false, "1 piece another delivery holds"]]);
  expect(shown.folds[0].rows).toEqual([
    `backend${LINK} · no change since ${READ}API-1: Backend 1buildingAPI-2: Backend 2merged`,
    `payments${LINK} · couldn't be read since ${ATTEMPT}API-1: Backend 1review`,
    `web${LINK} · never read`]);
  expect(shown.folds[1].rows).toEqual(["OPS-4: AlertingTo Do"]);
});

test("the built-in page keeps its done words when every sibling is fresh and landed, and draws nothing new without siblings", () => {
  expect(builtin(document([sibling("backend", stories("merged"))])).statuses).toEqual([]);
  expect(builtin(document([sibling("backend", stories("building"))], { landed: false })).statuses).toEqual([]);
  const plain = builtin(document(undefined));
  expect(plain.folds).toEqual([]);
  expect(plain.headings.includes("Across repositories")).toBe(false);
});

test("each outcome is said since it began, and a sibling tried but never read says so", async () => {
  const tried = { name: "web", link: LINK, addedAt: "2026-10-30T10:00:00Z", lastOutcome: "unavailable", lastAttemptAt: ATTEMPT };
  const [shown] = await open(document([sibling("api", stories("building"), "stale"), sibling("pay", stories("building"), "incompatible"), tried]));
  const heads = [...shown.app.querySelectorAll("#across .across-head")].map((node) => node.textContent);
  expect(heads).toEqual(["apiStale since 5 minutes ago", "payNeeds a compatible snapshot since 5 minutes ago",
    "webCouldn't be read since 5 minutes ago · never read"]);
  expect(said(shown).across).toEqual([`api has 1 open, couldn't be read since ${ATTEMPT}. pay has 1 open, couldn't be read since ${ATTEMPT}. ` +
    "web couldn't be read (never read)."]);
});

test("a link that isn't a web address is drawn as a name, a landed story is marked, and a story's card names its title", async () => {
  const [shown, hovered] = await open(document([sibling("backend", stories("building", "merged"), "read", { link: "javascript:alert(1)" })]),
    { steps: [{ hover: '#across [data-ticket="API-1"]' }] });
  const name = shown.app.querySelector("#across .across-name");
  expect(name.tagName).toBe("SPAN");
  expect(name.hasAttribute("href")).toBe(false);
  expect([...shown.app.querySelectorAll("#across li")].map((row) => row.className)).toEqual(["", "is-landed"]);
  const card = hovered.app.querySelector(".issue-card");
  expect(card.hidden).toBe(false);
  expect(card.textContent).toContain("Backend 1");
});
