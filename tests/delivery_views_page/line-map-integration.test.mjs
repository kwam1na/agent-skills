// A delivery that rejoins its target after its pieces land: the count's two beats under the name, the line past the
// integration base with its four stops and the target beneath, the ledger of proofs pinned to their heads, and the
// journey's share. Each is read from the record's meta.integration and its integration facts alone.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns, withAcross } from "./support.mjs";
import { LENSES, headOf, item, journal, record } from "./plan-fixtures.mjs";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const KEY = "APP-10";
const INTEGRATION = { base: "integrate/app", target: "main", remote: "origin" };
const [M, H1, H2, H3] = ["merge", "fix-1", "fix-2", "fix-3"].map((name) => headOf(name));

const landed = (subject, at) => [[at, "approve", subject], [at + 1, "candidate.handed", subject], [at + 2, "candidate.merged", subject]];
const merged = (at, head, conflicts = []) => [at, "integration.merged", KEY,
  { data: { target: "main", targetHead: headOf("main-" + at), head, conflicts } }];
const suite = (at, head, result = "passed", extra = {}) => [at, "integration.suite", KEY, { data: { head, result, seconds: 1200, ...extra } }];
const round = (at, head, verdicts = null, number = 1) => [at, "review.round", KEY,
  { round: number, data: { head }, ...(verdicts ? { verdicts } : {}) }];
const requested = (at, head) => [at, "integration.requested", KEY, { data: { changeRequest: "https://github.com/acme/app/pull/7", head } }];
const rejoined = (at) => [at, "integration.landed", KEY, { data: { target: "main", head: headOf("main-final") } }];

function delivery(steps, { open = false, integration = INTEGRATION, finish = [] } = {}) {
  const subjects = ["A", "B"];
  const entries = journal([...landed("A", 10), ...(open ? [] : landed("B", 20)), ...steps]);
  // The integration facts are the lead's, under the delivery's own key, with no lane.
  entries.forEach((entry) => { if (entry.subject === KEY) entry.lane = null; });
  return record({ queue: subjects.map((subject) => item(subject, open && subject === "B" ? "queued" : "merged")), journal: entries, finish,
    meta: { trackerParent: KEY, baseHead: headOf("base"), ...(integration ? { integration } : {}) } });
}
function withDrift(page, drift) {
  return page.replace("<script>\n", `<script type="application/json" id="delivery-integration">${JSON.stringify({ drift })}</script>\n<script>\n`);
}
async function shown(document, drift = null) {
  const page = pageWithRuns(document, {}, { config: CONFIG });
  const [first] = await openPage(drift ? withDrift(page, drift) : page, { now: NOW, store: false });
  return first.app;
}
const count = (app) => app.querySelector("#now .now-count").textContent;
const drift = (app) => app.querySelector("#now .say-drift")?.textContent ?? null;
const steps = (app) => [...app.querySelectorAll("#line .mg-step")].map((stop) => [stop.getAttribute("data-step"), stop.getAttribute("data-state")]);
const states = (app) => steps(app).map(([, state]) => state);
const row = (app, name) => app.querySelector(`#line .rj-row[data-row="${name}"]`);
const said = (app, name) => row(app, name).querySelector(".rj-said").textContent;
const struck = (app, name) => [...row(app, name).querySelectorAll(".rj-head")].map((head) => [head.textContent, head.tagName === "S"]);
const share = (app) => parseFloat(app.querySelector("#now .journey .done").style.width);
const short = (head) => head.slice(0, 7);
const near = (actual, expected) => expect(Math.round(actual * 1000)).toBe(Math.round(expected * 1000));

test("the count says the two beats once every piece has landed, today's count while one is open, and every piece landed after the landing", async () => {
  const rejoining = await shown(delivery([merged(30, M)]));
  expect(count(rejoining)).toBe("2 pieces landed beside main · Now it rejoins");
  expect(rejoining.querySelector("#now .now-count .title-beat").textContent).toBe("Now it rejoins");
  const open = await shown(delivery([], { open: true }));
  expect(count(open)).toBe("1 of 2 pieces have landed");
  expect(drift(open)).toBe(null);
  const done = await shown(delivery([merged(30, M), suite(31, M), round(32, M), requested(33, M), rejoined(34)]));
  expect(count(done)).toBe("Every piece landed");
  expect(done.querySelector("#line .rejoin")).toBe(null);
  expect(drift(done)).toBe(null);
});

test("the sentence under it counts the target's moves, the catch-ups and the gap, and leaves the counts out without drift", async () => {
  const twice = delivery([merged(30, M), merged(35, H1)]);
  expect(drift(await shown(twice, { moved: 12, behind: 4 })))
    .toBe("main moved 12 commits while this worked. The lead has caught up 2 times, and the gap is 4.");
  expect(drift(await shown(twice))).toBe("The lead has caught up 2 times.");
  expect(drift(await shown(delivery([merged(30, M)]), { moved: 1, behind: 0 })))
    .toBe("main moved 1 commit while this worked. The lead has caught up 1 time, and the gap is 0.");
});

test("a page that polls takes in the renderer's new drift counts, with no reload", async () => {
  const document = delivery([merged(30, M)]);
  const page = pageWithRuns(document, {}, { config: CONFIG });
  const snapshots = await openPage(withDrift(page, { moved: 5, behind: 2 }), { now: NOW,
    rendered: withDrift(page, { moved: 9, behind: 6 }), steps: [{ push: document }, { wait: 3100 }] });
  expect(drift(snapshots[0].app)).toBe("main moved 5 commits while this worked. The lead has caught up 1 time, and the gap is 2.");
  expect(drift(snapshots[2].app)).toBe("main moved 9 commits while this worked. The lead has caught up 1 time, and the gap is 6.");
});

test("the line draws four stops past the join, labelled by the base, and the target beneath to the finish post", async () => {
  const app = await shown(delivery([]));
  expect(steps(app)).toEqual([["merged", "next"], ["suite", "hollow"], ["round", "hollow"], ["requested", "hollow"]]);
  expect([...app.querySelectorAll("#line .mg-step-label")].map((label) => label.textContent)).toEqual(["main in", "suite", "review", "change request"]);
  expect(app.querySelector("#line .mg-base").textContent).toBe("integrate/app");
  expect(app.querySelector("#line .mg-target-label").textContent).toBe("main");
  const target = app.querySelector("#line .mg-target").getAttribute("d");
  const post = app.querySelector("#line .mg-post").getAttribute("d");
  // The post stands across the target's end, beneath the main line.
  const [, targetY, endX] = /^M(\d+(?:\.\d+)?) (\d+(?:\.\d+)?)H(\d+(?:\.\d+)?)$/.exec(target).slice(1).map(Number);
  const [postX, postTop, postBottom] = /^M(\d+(?:\.\d+)?) (\d+(?:\.\d+)?)V(\d+(?:\.\d+)?)$/.exec(post).slice(1).map(Number);
  expect([postX, (postTop + postBottom) / 2]).toEqual([endX, targetY]);
  expect(app.querySelector("#line .lines-track").classList.contains("rejoining")).toBe(true);
});

test("a merge stands until the next merge: a later suite at another head strikes nothing of Caught up", async () => {
  const app = await shown(delivery([merged(30, M), suite(31, H2)]));
  expect(states(app)).toEqual(["filled", "filled", "next", "hollow"]);
  expect(struck(app, "caught")).toEqual([[short(M), false]]);
  expect(struck(app, "suite")).toEqual([[short(H2), false]]);
});

test("a fix recorded after a round strikes the round and empties the review stop, and the target in stays filled", async () => {
  const app = await shown(delivery([merged(30, M), suite(31, H1), round(32, H1), suite(33, H3)]));
  expect(states(app)).toEqual(["filled", "filled", "next", "hollow"]);
  expect(struck(app, "review")).toEqual([[short(H1), true]]);
  expect(struck(app, "suite")).toEqual([[short(H3), false]]);
});

test("a round as the newest fact strikes an earlier suite", async () => {
  const app = await shown(delivery([merged(30, M), suite(31, H1), round(32, H2)]));
  expect(struck(app, "suite")).toEqual([[short(H1), true]]);
  expect(struck(app, "review")).toEqual([[short(H2), false]]);
  expect(states(app)).toEqual(["filled", "next", "filled", "hollow"]);
});

test("a catch-up after a fix strikes the suite, the round and the earlier merge", async () => {
  const app = await shown(delivery([merged(30, M), round(31, M), suite(32, H2), merged(33, H3)]));
  expect(struck(app, "suite")).toEqual([[short(H2), true]]);
  expect(struck(app, "review")).toEqual([[short(M), true]]);
  expect(struck(app, "caught")).toEqual([[short(M), true], [short(H3), false]]);
  expect(states(app)).toEqual(["filled", "next", "hollow", "hollow"]);
});

test("a request superseded by a later suite is struck and its stop hollow", async () => {
  const app = await shown(delivery([merged(30, M), requested(31, H1), suite(32, H2)]));
  expect(states(app)).toEqual(["filled", "filled", "next", "hollow"]);
  expect(said(app, "rejoin")).toBe("Needs an approving round at the current head.");
});

test("what stands at the current head rings its stop and says what the rejoin still needs", async () => {
  const failed = await shown(delivery([merged(30, M), suite(31, M, "failed")]));
  expect(states(failed)).toEqual(["filled", "danger", "next", "hollow"]);
  expect(said(failed, "suite")).toBe(`failed in 20 minutes at ${short(M)}`);
  expect(said(failed, "rejoin")).toBe("Needs a passing suite at the current head and an approving round at it.");

  const changes = await shown(delivery([merged(30, M), suite(31, M), round(32, M, { [LENSES[0]]: "approve", [LENSES[1]]: "changes" })]));
  expect(states(changes)).toEqual(["filled", "filled", "warning", "next"]);
  expect(said(changes, "review")).toBe(`round 1 ${LENSES[1]} asks for changes, at ${short(M)}`);
  expect([...row(changes, "review").querySelectorAll(".rj-dot")].map((dot) => dot.className)).toEqual(["rj-dot approve", "rj-dot changes"]);
  expect(said(changes, "rejoin")).toBe("Needs an approving round at the current head.");

  const over = await shown(delivery([merged(30, M), suite(31, M, "over-budget", { budgetSeconds: 1000 }), round(32, M)]));
  expect(states(over)).toEqual(["filled", "filled", "filled", "next"]);
  expect(said(over, "suite")).toBe(`over-budget in 20 minutes against a budget of 17 minutes at ${short(M)}`);
  expect(said(over, "rejoin")).toBe("Ready. A change request into main can open. The merge is the operator's.");

  const unreadable = await shown(delivery([merged(30, M), suite(31, M, "unreadable")]));
  expect(states(unreadable)).toEqual(["filled", "danger", "next", "hollow"]);
  expect(said(unreadable, "suite").startsWith("unreadable")).toBe(true);
  expect(said(unreadable, "rejoin")).toBe("Needs a passing suite at the current head and an approving round at it.");

  const ready = await shown(delivery([merged(30, M), suite(31, H1), round(32, H1), requested(33, H1)]));
  expect(states(ready)).toEqual(["filled", "filled", "filled", "filled"]);
  expect(said(ready, "rejoin")).toBe(`Ready. The change request https://github.com/acme/app/pull/7 is open at ${short(H1)}. The merge is the operator's.`);

  const apart = await shown(delivery([merged(30, M), suite(31, H1), round(32, H2)]));
  expect(said(apart, "rejoin")).toBe("Needs a passing suite and an approving round at one head.");
  // Both at one head that a later request moved on from: what is missing is both, at the current head.
  const moved = await shown(delivery([merged(30, M), suite(31, H1), round(32, H1), requested(33, H2)]));
  expect(said(moved, "rejoin")).toBe("Needs a passing suite at the current head and an approving round at it.");
});

test("a row with no fact yet says what will make it", async () => {
  const app = await shown(delivery([]));
  expect(["caught", "suite", "review", "rejoin"].map((name) => said(app, name))).toEqual([
    "Not yet: the lead merges main in and records it.", "Not yet: the lead runs the full suite at the integration's head.",
    "Not yet: the integration review records its first round.",
    "Needs a passing suite at the current head and an approving round at it."]);
  expect(app.querySelector("#line .rejoin-title").textContent).toBe("Before it rejoins");
});

test("the journey's share counts the filled stops, gives back a superseded one, and is full after the landing", async () => {
  const finish = [{ id: "line", text: "The suite passed", met: true }];
  // Two pieces, one met finish line and four stops: 3 of 7 before any stop.
  near(share(await shown(delivery([], { finish }))), 3 / 7 * 100);
  near(share(await shown(delivery([merged(30, M), suite(31, M)], { finish }))), 5 / 7 * 100);
  near(share(await shown(delivery([merged(30, M), suite(31, M), round(32, H1)], { finish }))), 5 / 7 * 100);
  near(share(await shown(delivery([merged(30, M), rejoined(31)], { finish }))), 100);
  // A record without an integration keeps today's share.
  near(share(await shown(delivery([], { finish, integration: null }))), 100);
});

test("a round under the delivery's key on a record without an integration draws nothing of it", async () => {
  const app = await shown(delivery([round(30, M)], { integration: null }));
  expect(count(app)).toBe("Every piece has landed");
  expect(app.querySelector("#line .mg-step")).toBe(null);
  expect(app.querySelector("#line .rejoin")).toBe(null);
  expect(app.querySelector("#line .mg-target")).toBe(null);
  expect(drift(app)).toBe(null);
});

test("a delivery with a same-epic sibling draws the rejoin while it rejoins, and the epic's post once it closes", async () => {
  // A sibling as reconcile leaves it: read, with one story still open.
  const api = { name: "api", link: "https://views.example/d/" + "a".repeat(64), addedAt: "2026-10-30T10:00:00Z", lastOutcome: "read",
    lastAttemptAt: "2026-10-30T11:55:00Z", readAt: "2026-10-30T11:50:00Z", stories: [{ subject: "API-1", title: "api 1", stage: "building" }] };
  const withSibling = (facts) => {
    const doc = delivery(facts);
    doc.state.siblings = [api];
    return withAcross(doc);
  };
  const approved = Object.fromEntries(LENSES.map((lens) => [lens, "approve"]));
  const width = (app) => app.querySelector("#line .lines-track").style.getPropertyValue("--merge-width");
  for (const facts of [[merged(30, M)], [merged(30, M), suite(31, M), round(32, M, approved), requested(33, M), rejoined(34)]]) {
    const app = await shown(withSibling(facts));
    expect(app.querySelector("#line .lines-track").classList.contains("rejoining")).toBe(true);
    expect(steps(app).length).toBe(4);
    expect(width(app)).toBe("478px");
    expect([app.querySelector("#line .mg-epic"), app.querySelector("#line .mg-epic-post"), app.querySelector("#line .line.sibling")])
      .toEqual([null, null, null]);
    expect(app.querySelector("#line .line-merge").getAttribute("aria-label")).not.toContain("the epic");
  }
  const closed = await shown(withSibling([merged(30, M), suite(31, M), round(32, M, approved), requested(33, M), rejoined(34), [40, "delivery.closed", null]]));
  expect(closed.querySelector("#line .lines-track").classList.contains("rejoining")).toBe(false);
  expect(closed.querySelector("#line .mg-step")).toBe(null);
  expect(width(closed)).toBe("304px");
  expect(closed.querySelector("#line .mg-epic-post") !== null).toBe(true);
  expect(closed.querySelectorAll("#line .line.sibling").length).toBe(1);
});
