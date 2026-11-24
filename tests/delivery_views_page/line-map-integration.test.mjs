// A delivery that rejoins its target after its pieces land: the count's two beats under the name, the line past the
// integration base with its four stops and the target beneath, the ledger of proofs pinned to their heads, and the
// journey's share. Each is read from the record's meta.integration and its integration facts alone.
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { REPLAYED_RUNS, ROOT, expect, openPage, pageWithRuns, withAcross } from "./support.mjs";
import { LENSES, headOf, item, journal, minute, record } from "./plan-fixtures.mjs";

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
// Each step of the rejoin bar names itself, "<step>: <what its latest fact shows>", as the "Before it rejoins" rows did.
const STEP_OF = { caught: "merged", suite: "suite", review: "round", rejoin: "requested" };
const segment = (app, name) => app.querySelector(`#line .mg-step[data-step="${STEP_OF[name]}"]`);
const said = (app, name) => { const label = segment(app, name).getAttribute("aria-label"); return label.slice(label.indexOf(": ") + 2); };
// The heads a step's words name, each with whether a later fact moved the integration on from it.
const struck = (app, name) => [...said(app, name).matchAll(/\b([0-9a-f]{7})(, since moved on)?/g)].map((found) => [found[1], Boolean(found[2])]);
const under = (app) => [...app.querySelectorAll("#line .mg-step-said")].map((line) => line.textContent).join(" ");
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
  // Rejoined, nothing waits on a change request any more: none is offered under the bar.
  expect(done.querySelector("#line .mg-change-label")).toBe(null);
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

test("the line draws one bar of four steps past the ticks, labelled by the step it is on, and bends into the target at the finish post", async () => {
  const app = await shown(delivery([]));
  expect(steps(app)).toEqual([["merged", "next"], ["suite", "hollow"], ["round", "hollow"], ["requested", "hollow"]]);
  expect([...app.querySelectorAll("#line .mg-step-label")].map((label) => label.textContent)).toEqual(["Rejoin main · main in"]);
  expect([...app.querySelectorAll("#line .mg-step")].map((step) => step.getAttribute("aria-label").split(":")[0]))
    .toEqual(["Main in", "Suite", "Review", "Change request"]);
  // The target is drawn once, from the post, so its name is not repeated and the base's name is not drawn.
  expect([...app.querySelectorAll("#line .mg-target-label")].map((label) => label.textContent)).toEqual(["main"]);
  expect(app.querySelector("#line .mg-base")).toBe(null);
  const target = app.querySelector("#line .mg-target").getAttribute("d");
  const post = app.querySelector("#line .mg-post").getAttribute("d");
  const trunk = app.querySelector("#line .mg-trunk").getAttribute("d");
  // The post stands across the target's start, beneath the main line, where the main line bends into it.
  const [startX, targetY] = /^M(\d+(?:\.\d+)?) (\d+(?:\.\d+)?)H/.exec(target).slice(1).map(Number);
  const [postX, postTop, postBottom] = /^M(\d+(?:\.\d+)?) (\d+(?:\.\d+)?)V(\d+(?:\.\d+)?)$/.exec(post).slice(1).map(Number);
  expect([postX, (postTop + postBottom) / 2]).toEqual([startX, targetY]);
  expect(trunk.endsWith(` ${postX} ${targetY}`)).toBe(true);
  // The bar starts past the last tick.
  const lastTick = Math.max(...[...app.querySelectorAll("#line .mg-tick-mark")].map((mark) => Number(mark.getAttribute("x"))));
  expect(Number(app.querySelector("#line .mg-step").getAttribute("x"))).toBeGreaterThan(lastTick);
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
  // A request that no longer stands at the current head is not offered as the open one.
  expect(app.querySelector("#line .mg-change-label")).toBe(null);
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
  // Every step is in but it hasn't rejoined: the bar is on its change request, says what it waits on, and links it.
  expect(ready.querySelector("#line .mg-bar-label").textContent).toBe("Rejoin main · change request");
  expect(under(ready)).toBe(`Change request. Ready. The change request https://github.com/acme/app/pull/7 is open at ${short(H1)}. The merge is the operator's.`
    .split(" ").reduce((lines, word) => { const last = lines[lines.length - 1]; if (last && (last + " " + word).length > 34) lines.push(word); else lines[lines.length - 1] = last ? last + " " + word : word; return lines; }, [""])
    .slice(0, 3).join(" "));
  // With no forge configured it is named as text, as every change request on the page is; with one, it is linked.
  expect([ready.querySelector("#line a.mg-change"), ready.querySelector("#line .mg-change-label").textContent])
    .toEqual([null, "Change request https://github.com/acme/app/pull/7"]);
  const forge = { name: "GitHub", repository: "acme/app", pullUrl: "https://github.com/{repository}/pull/{number}",
    commitUrl: "https://github.com/{repository}/commit/{sha}" };
  const readyDoc = delivery([merged(30, M), suite(31, H1), round(32, H1), requested(33, H1)]);
  const [{ app: linked }] = await openPage(pageWithRuns(readyDoc, {}, { config: { ...CONFIG, forges: [forge] } }), { now: NOW, store: false });
  const link = linked.querySelector("#line a.mg-change");
  expect([link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel"), link.textContent])
    .toEqual(["https://github.com/acme/app/pull/7", "_blank", "noopener noreferrer", "Change request #7"]);
  expect(link.getAttribute("aria-label")).toBe("Open the change request into main #7");
  // Only the step the bar is on reads now: a failed suite is "now", the review after it "later".
  expect([...failed.querySelectorAll("#line .mg-step")].map((step) => step.getAttribute("data-state"))).toEqual(["filled", "danger", "next", "hollow"]);

  const apart = await shown(delivery([merged(30, M), suite(31, H1), round(32, H2)]));
  expect(said(apart, "rejoin")).toBe("Needs a passing suite and an approving round at one head.");
  // Both at one head that a later request moved on from: what is missing is both, at the current head.
  const moved = await shown(delivery([merged(30, M), suite(31, H1), round(32, H1), requested(33, H2)]));
  expect(said(moved, "rejoin")).toBe("Needs a passing suite at the current head and an approving round at it.");
});

test("a step with no fact yet says what will make it, and the bar's own status is the current step's", async () => {
  const app = await shown(delivery([]));
  expect(["caught", "suite", "review", "rejoin"].map((name) => said(app, name))).toEqual([
    "Not yet: the lead merges main in and records it.", "Not yet: the lead runs the full suite at the integration's head.",
    "Not yet: the integration review records its first round.",
    "Needs a passing suite at the current head and an approving round at it."]);
  expect(under(app)).toBe("Main in. Not yet: the lead merges main in and records it.");
  const suiteNext = await shown(delivery([merged(30, M)]));
  expect(under(suiteNext)).toBe("Suite. Not yet: the lead runs the full suite at the integration's head.");
  // The "Before it rejoins" table is gone: the bar holds what it said.
  expect(app.querySelector("#line .rejoin, #line .rejoin-title, #line .rj-row")).toBe(null);
  expect(app.textContent).not.toContain("Before it rejoins");
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
    expect(width(app)).toBe(app.querySelector("#line .line-merge").getAttribute("width") + "px");
    expect([app.querySelector("#line .mg-epic"), app.querySelector("#line .mg-epic-post"), app.querySelector("#line .line.sibling")])
      .toEqual([null, null, null]);
    expect(app.querySelector("#line .line-merge").getAttribute("aria-label")).not.toContain("the epic");
  }
  const closed = await shown(withSibling([merged(30, M), suite(31, M), round(32, M, approved), requested(33, M), rejoined(34), [40, "delivery.closed", null]]));
  expect(closed.querySelector("#line .lines-track").classList.contains("rejoining")).toBe(false);
  expect(closed.querySelector("#line .mg-step")).toBe(null);
  expect(width(closed)).toBe(closed.querySelector("#line .line-merge").getAttribute("width") + "px");
  expect(closed.querySelector("#line .mg-epic-post") !== null).toBe(true);
  expect(closed.querySelectorAll("#line .line.sibling").length).toBe(1);
});

// A suite's start, as lead-suite journals it: its head, when it began and, from the last finished run under the same
// budget, how long it usually takes. NOW is minute 360, so a start at minute 326 has run 34 minutes.
const started = (at, head, extra = {}) => [at, "integration.suite.started", KEY,
  { data: { head, startedAt: minute(at), expectedSeconds: 31 * 60, budgetSeconds: 29 * 60, ...extra } }];
const label = (app) => app.querySelector("#line .mg-bar-label").textContent;
const box = (node) => ["x", "width"].map((name) => Number(node.getAttribute(name)));
const usualAt = (app) => Number(app.querySelector("#line .mg-suite-usual").getAttribute("d").match(/^M([\d.]+)/)[1]);
const filledTo = (app) => Number(app.querySelector("#line #mg-suite-clip rect").getAttribute("width"));

test("a suite running at the integration's head draws its step wide and striped, with a tick at its usual time", async () => {
  const app = await shown(delivery([merged(30, M), started(326, M)]));
  expect(label(app)).toBe("Rejoin main · suite running");
  expect(under(app)).toBe(`The full suite is running at ${short(M)}. 34 min, about 31 usual, so 3 over.`);
  // Nothing else is drawn as next while the suite runs.
  expect(states(app)).toEqual(["filled", "running", "hollow", "hollow"]);
  expect(said(app, "suite")).toBe(`running at ${short(M)} for 34 min, about 31 usual, so 3 over`);
  const [suiteX, suiteWidth] = box(segment(app, "suite"));
  const [, caughtWidth] = box(segment(app, "caught"));
  expect(suiteWidth > 2 * caughtWidth).toBe(true);
  expect(app.querySelector("#line .mg-suite-run .mg-stripes .mg-stripe") !== null).toBe(true);
  expect(app.querySelector("#line .mg-suite-run").getAttribute("aria-hidden")).toBe("true");
  // Past its usual time the stripe fills the step, and the tick sits where 31 of 34 minutes falls.
  near(filledTo(app), suiteWidth);
  near(usualAt(app), suiteX + suiteWidth * 31 / 34);
  // The four steps still add up to the bar.
  const last = box(segment(app, "rejoin"));
  near(last[0] + last[1] - box(segment(app, "caught"))[0], 140);
});

test("before its usual time the stripe fills the step as the run nears it, and the sentence names no overrun", async () => {
  const early = await shown(delivery([merged(30, M), started(350, M)]));
  expect(under(early)).toBe(`The full suite is running at ${short(M)}. 10 min, about 31 usual.`);
  const [x, width] = box(segment(early, "suite"));
  near(filledTo(early), width * 10 / 31);
  near(usualAt(early), x + width);
  // Within a minute of the usual time nothing is over yet.
  expect(under(await shown(delivery([merged(30, M), started(329, M)])))).toBe(`The full suite is running at ${short(M)}. 31 min, about 31 usual.`);
  // With no earlier run to go by, it says how long it has run, and draws no tick.
  const first = await shown(delivery([merged(30, M), started(326, M, { expectedSeconds: undefined })]));
  expect(under(first)).toBe(`The full suite is running at ${short(M)}. 34 min.`);
  expect(first.querySelector("#line .mg-suite-usual")).toBe(null);
  near(filledTo(first), box(segment(first, "suite"))[1]);
});

test("a timed suite's finish draws passed solid, over its budget with a cap, and failed in the setback colour", async () => {
  const passed = await shown(delivery([merged(30, M), started(326, M), suite(355, M)]));
  expect(states(passed)).toEqual(["filled", "filled", "next", "hollow"]);
  expect(label(passed)).toBe("Rejoin main · review");
  expect(passed.querySelector("#line .mg-suite-run, #line .mg-suite-usual, #line .mg-suite-over")).toBe(null);
  expect(box(segment(passed, "suite"))[1] > 2 * box(segment(passed, "caught"))[1]).toBe(true);
  const over = await shown(delivery([merged(30, M), started(326, M), suite(355, M, "over-budget", { budgetSeconds: 29 * 60 })]));
  expect(states(over)).toEqual(["filled", "filled", "next", "hollow"]);
  const [x, width] = box(segment(over, "suite"));
  const cap = box(over.querySelector("#line .mg-suite-over"));
  near(cap[0] + cap[1], x + width);
  const failed = await shown(delivery([merged(30, M), started(326, M), suite(355, M, "failed")]));
  expect(states(failed)).toEqual(["filled", "danger", "next", "hollow"]);
  expect(failed.querySelector("#line .mg-suite-run, #line .mg-suite-over")).toBe(null);
  // A suite with no start keeps the even bar, and an untimed run over its budget draws no cap.
  const untimed = await shown(delivery([merged(30, M), suite(355, M, "over-budget", { budgetSeconds: 29 * 60 })]));
  expect(box(segment(untimed, "suite"))[1]).toBe(box(segment(untimed, "caught"))[1]);
  expect(untimed.querySelector("#line .mg-suite-over")).toBe(null);
});

test("a later start or finish supersedes an unfinished start, and a newer head leaves it behind", async () => {
  const again = await shown(delivery([merged(30, M), started(300, M), merged(320, H1), started(326, H1)]));
  expect(under(again)).toBe(`The full suite is running at ${short(H1)}. 34 min, about 31 usual, so 3 over.`);
  const finished = await shown(delivery([merged(30, M), started(326, M), suite(340, M)]));
  expect(finished.querySelector("#line .mg-suite-run")).toBe(null);
  // The target merged in again while the suite ran: that run is of a head the integration moved on from.
  const movedOn = await shown(delivery([merged(30, M), started(326, M), merged(340, H1)]));
  expect(states(movedOn)).toEqual(["filled", "next", "hollow", "hollow"]);
  expect(label(movedOn)).toBe("Rejoin main · suite");
  expect(box(segment(movedOn, "suite"))[1]).toBe(box(segment(movedOn, "caught"))[1]);
  // A finish at another head than the start's draws no timed step.
  const elsewhere = await shown(delivery([merged(30, M), started(326, M), suite(340, H1)]));
  expect(box(segment(elsewhere, "suite"))[1]).toBe(box(segment(elsewhere, "caught"))[1]);
});

test("the running stripe stands still under reduced motion", () => {
  const css = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "line-map.css"), "utf8");
  expect(css).toContain("@media (prefers-reduced-motion: reduce) { .mg-stripes { animation: none; } }");
  expect(css).toContain(".mg-stripes { animation: mg-crawl 1.2s linear infinite; }");
});
