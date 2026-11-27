// The runs page, rendered by the shipped renderer from the runtime's captured output (tests/fixtures/run-events/runs) and opened as a browser would.
import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { ROOT, RUNS_CORPUS, block, expect, openPage, readJson, renderRunsPage, served } from "./support.mjs";

process.env.TZ = "UTC";
// The goldens are drawn with no plan preview, whatever the shell running the lane holds.
delete process.env.DELIVERY_PLAN_PREVIEWS;

const COMMANDS = readJson(path.join(RUNS_CORPUS, "runs-commands.json"));
const ABSENT = { spec: "delivery-state-absent/1" };
const INSIDE_PLAN = "run-open-execute-inside-plan-1";
const AGENT = "run-open-waiting-on-an-agent-1";
const REVIEW_AGAIN = "run-open-review-entered-again-1";
const EXECUTE_AGAIN = "run-open-execute-entered-again-1";
const ENDED = "run-ended-plan-then-execute-1";
const LEFT_OPEN = "run-displaced-then-partial-1";
const PARTIAL = "run-displaced-then-partial-2";
// The exact bytes of the data line the live server sends with `event: runs`.
const RUNS_EVENT = '{"generation": 1}';

const PAGE = renderRunsPage(ABSENT, { runsCommands: COMMANDS });

function runNode(app, runId) { return app.querySelector(`[data-run="${runId}"]`); }
function stagesOf(app, runId) { return [...runNode(app, runId).querySelectorAll(".stage")].map((node) => node.getAttribute("data-stage")); }
function ringed(app, runId) { return [...runNode(app, runId).querySelectorAll(".stage.current")].map((node) => node.getAttribute("data-stage")); }

test("Now lists the open runs before the ended ones, each newest first", async () => {
  const [shown] = await openPage(PAGE);
  assert.deepEqual([...shown.app.querySelectorAll("#now [data-run]")].map((node) => node.getAttribute("data-run")),
    [INSIDE_PLAN, EXECUTE_AGAIN, REVIEW_AGAIN, AGENT]);
  // Two ended runs, and the run they displaced: the newest first.
  assert.deepEqual([...shown.app.querySelectorAll("#ended [data-run]")].map((node) => node.getAttribute("data-run")),
    [ENDED, LEFT_OPEN, PARTIAL]);
  assert.deepEqual([...shown.app.querySelectorAll("[data-run]")].map((node) => node.getAttribute("data-section")),
    ["now", "now", "now", "now", "ended", "ended", "ended"]);
});

test("Needs you appears only on the unresolved human wait, and the agent wait still says what and what next", async () => {
  const [shown] = await openPage(PAGE);
  assert.deepEqual([...shown.app.querySelectorAll(".needs-you")].map((node) => node.closest("[data-run]").getAttribute("data-run")), [INSIDE_PLAN]);
  assert.match(runNode(shown.app, INSIDE_PLAN).querySelector(".wait.needs").textContent,
    /^Needs you Pushing the branch needs the operator's grant\. Next: Grant the push or keep the branch local\.$/);
  const agent = runNode(shown.app, AGENT).querySelector(".wait");
  assert.equal(agent.textContent, "Waiting on agent A subagent is reproducing the failure. Next: Compare its observation with the prediction.");
  assert.equal(agent.querySelector(".needs-you"), null);
  // The ended run's wait was resolved before it ended, so it shows none.
  assert.equal(runNode(shown.app, ENDED).querySelector(".wait"), null);
});

test("a run's line draws exactly its stages, the current one ringed, and never a runtime phase", async () => {
  const [shown] = await openPage(PAGE);
  assert.deepEqual(stagesOf(shown.app, INSIDE_PLAN), ["plan", "execute", "review"]);
  assert.deepEqual(ringed(shown.app, INSIDE_PLAN), ["execute"]);
  assert.equal(runNode(shown.app, INSIDE_PLAN).querySelector('[data-stage="verify"], [data-stage="prepare"]'), null);
  assert.equal(runNode(shown.app, INSIDE_PLAN).querySelector(".doing").textContent, "Fix the round's finding.");
  // Each re-entry draws each stage once, in first-entry order, with the stage entered again ringed.
  assert.deepEqual(stagesOf(shown.app, REVIEW_AGAIN), ["plan", "execute", "review"]);
  assert.deepEqual(ringed(shown.app, REVIEW_AGAIN), ["review"]);
  assert.deepEqual(stagesOf(shown.app, EXECUTE_AGAIN), ["plan", "execute", "review"]);
  assert.deepEqual(ringed(shown.app, EXECUTE_AGAIN), ["execute"]);
  assert.deepEqual(ringed(shown.app, ENDED), []);
});

test("the displaced run shows under the ended runs as left open and replaced", async () => {
  const [shown] = await openPage(PAGE);
  const left = shown.app.querySelector(`#ended [data-run="${LEFT_OPEN}"]`);
  assert.match(left.querySelector(".state").textContent, /^Left open, replaced · /);
  assert.equal(shown.app.querySelector(`#now [data-run="${LEFT_OPEN}"]`), null);
  assert.match(runNode(shown.app, PARTIAL).querySelector(".state").textContent, /^Ended partial · /);
});

test("the detail panel draws the gates, decisions, blockers, change request, rounds and result", async () => {
  const index = [...(await openPage(PAGE))[0].app.querySelectorAll("[data-run]")].findIndex((node) => node.getAttribute("data-run") === ENDED);
  const [closed, opened] = await openPage(PAGE, { steps: [{ click: "[data-run] > .run-head", index }] });
  assert.equal(runNode(closed.app, ENDED).querySelector(".run-detail").hasAttribute("hidden"), true);
  const panel = runNode(opened.app, ENDED).querySelector(".run-detail");
  assert.equal(panel.hasAttribute("hidden"), false);
  assert.deepEqual([...panel.querySelectorAll(".gate")].map((gate) => gate.textContent),
    ["scripts/validate-registry.py pass 4s", "scripts/run-tests.py fail 10m 15s"]);
  // The decision as the run recorded it: its fork, then its choice.
  const decided = JSON.parse(COMMANDS[`runs show ${ENDED} --json`]).events.find((event) => event.kind === "decision.recorded").payload;
  assert.equal(panel.querySelector(".decisions").textContent, `${decided.fork}: ${decided.choice}`);
  assert.equal(panel.querySelector(".blockers").textContent, "One deferral has no tracked follow-up. review.deferral-untracked");
  assert.equal(panel.querySelector(".change a").getAttribute("href"), "https://github.com/example/repository/pull/1");
  assert.ok(panel.querySelector(".run-card, .run-summary, [class^='run-']"), "the run summary's rounds are drawn");
  assert.match(panel.textContent, /Round 1/);
  assert.equal(panel.querySelector(".result").textContent, "complete · 152352 subagent-tokens · coverage partial");
  assert.equal(panel.querySelector(".history-link a").getAttribute("href"), "delivery-history.html");
});

test("a displaced run's panel says it has no result, never that it is still running", async () => {
  const [shown] = await openPage(PAGE);
  assert.equal(runNode(shown.app, LEFT_OPEN).querySelector(".result").textContent, "No result recorded.");
  assert.equal(runNode(shown.app, INSIDE_PLAN).querySelector(".result").textContent, "Still running.");
});

test("a page told the store isn't watched says so", async () => {
  const [shown] = await openPage(renderRunsPage(ABSENT, { runsCommands: COMMANDS, watched: "unwatched" }));
  assert.match(shown.app.querySelector(".unwatched").textContent, /^Runs aren't being watched/);
  assert.equal((await openPage(PAGE))[0].app.querySelector(".unwatched"), null);
});

test("through the injected adapter, a runs event re-fetches the page and the open panel shows the new gate", async () => {
  const before = served(PAGE);
  const show = JSON.parse(COMMANDS[`runs show ${INSIDE_PLAN} --json`]);
  show.events.push({ version: "run-event/2", eventId: "gate-tests", runId: INSIDE_PLAN, seq: 17, at: "2026-11-20T08:00:03Z",
    kind: "gate.reported", actor: { role: "executor" }, attestation: "self",
    payload: { command: "scripts/run-tests.py", outcome: "pass", durationMs: 2500 } });
  const after = served(renderRunsPage(ABSENT, { runsCommands: { ...COMMANDS, [`runs show ${INSIDE_PLAN} --json`]: JSON.stringify(show) } }));
  const gates = (snapshot) => [...runNode(snapshot.app, INSIDE_PLAN).querySelectorAll(".gate code")].map((node) => node.textContent);
  // Opened at the front page, as the live server serves it while there is no record: once a record reads, "/" is the line
  // map, so the page reads itself again from its own view, and nothing answers anywhere else.
  const result = await openPage(before, { server: { document: {} }, rendered: after, address: "http://localhost/",
    fetchAt: "/view/runs.html", steps: [{ click: "[data-run] > .run-head", index: 0 }, { runs: RUNS_EVENT, wait: 50 }] });
  const [, opened, pushed] = result;
  assert.deepEqual(gates(opened), ["scripts/validate-registry.py"]);
  assert.equal(runNode(pushed.app, INSIDE_PLAN).querySelector(".run-detail").hasAttribute("hidden"), false);
  assert.deepEqual(gates(pushed), ["scripts/validate-registry.py", "scripts/run-tests.py"]);
  assert.deepEqual(result.fetched, ["/view/runs.html"]);
});

test("a pushed record re-fetches the runs page too, so a run's piece follows the record", async () => {
  const result = await openPage(served(PAGE), { server: { document: {} }, rendered: served(PAGE), address: "http://localhost/",
    fetchAt: "/view/runs.html", steps: [{ event: { state: {} }, wait: 50 }] });
  assert.deepEqual(result.fetched, ["/view/runs.html"]);
});

test("with no record, the line map and history say so, link the runs page's view when served, and reload on a record", async () => {
  const pages = ["line-map", "history"].map((view) => renderRunsPage(ABSENT, { runsCommands: COMMANDS, view }));
  for (const page of pages) {
    const [file] = await openPage(page, { store: null });
    assert.equal(file.app.querySelector("h1").textContent, "No delivery is under way.");
    assert.equal(file.app.querySelector("#runs-link").getAttribute("href"), "runs.html");
    const opened = await openPage(served(page), { server: { document: {} }, rendered: page, steps: [{ wait: 5 }, { event: { state: {} } }] });
    const [servedAt, waited] = opened;
    assert.equal(servedAt.app.querySelector("#runs-link").getAttribute("href"), "/view/runs.html");
    assert.equal(waited.app.querySelector("h1").textContent, "No delivery is under way.");
    // One reload in all: the pushed record's.
    assert.equal(opened.navigations.length, 1);
  }
});

test("the runs golden pins the page whole", async () => {
  const golden = path.join(RUNS_CORPUS, "golden.json");
  // The page says when each round ran relative to today, so the golden is read at the view's own asOf, not today.
  const [shown] = await openPage(PAGE, { now: "2026-11-20T10:00:00Z" });
  const captured = { block: block(PAGE, "delivery-run-page"), view: block(PAGE, "delivery-run-page-view"), text: shown.text };
  if (process.env.RECAPTURE_RUNS_GOLDEN === "1" || !existsSync(golden)) {
    writeFileSync(golden, JSON.stringify(captured, null, 2) + "\n");
    assert.fail("captured the runs golden; check it in and run again");
  }
  assert.deepEqual(captured, readJson(golden));
});

// The plan previews the live server names, as render.py runs reads them from DELIVERY_PLAN_PREVIEWS.
const PREVIEWS = [
  { live: "127.0.0.1:4100", plan: "docs/plans/2026-11-23-a.md", title: "Plan A", hosted: "https://views.example/d/" + "a".repeat(64) },
  // A preview whose preview.json could not be read: no title and no hosted link, and its plan is its folder's name.
  { live: "127.0.0.1:4101", plan: "b-slug" },
];

function renderWithPreviews(previews) {
  process.env.DELIVERY_PLAN_PREVIEWS = JSON.stringify(previews);
  try { return renderRunsPage(ABSENT, { runsCommands: COMMANDS }); } finally { delete process.env.DELIVERY_PLAN_PREVIEWS; }
}

function previewRows(app) {
  return [...app.querySelectorAll("#previews .preview")].map((row) => ({
    plan: row.getAttribute("data-plan"), name: row.querySelector(".preview-name b").textContent,
    links: [...row.querySelectorAll(".preview-links a")].map((link) => [link.textContent, link.getAttribute("href")]) }));
}

test("with no preview there is no Plans in review section", async () => {
  const [shown] = await openPage(PAGE);
  assert.equal(shown.app.querySelector("#previews"), null);
  assert.doesNotMatch(shown.text, /Plans in review/);
});

test("Plans in review lists one row per preview, before Now, with its local and, when named, hosted link", async () => {
  const [shown] = await openPage(renderWithPreviews(PREVIEWS));
  const section = shown.app.querySelector("#previews");
  assert.equal(section.querySelector("h2").textContent, "Plans in review");
  assert.ok(section.compareDocumentPosition(shown.app.querySelector("#now")) & 4, "Plans in review comes before Now");
  assert.deepEqual(previewRows(shown.app), [
    { plan: "docs/plans/2026-11-23-a.md", name: "Plan A",
      links: [["Live view", "http://127.0.0.1:4100/"], ["Hosted view", "https://views.example/d/" + "a".repeat(64)]] },
    { plan: "b-slug", name: "b-slug", links: [["Live view", "http://127.0.0.1:4101/"]] },
  ]);
  // The plan file is shown under a title, never twice.
  assert.equal(section.querySelectorAll(".preview-name small").length, 1);
  // One sentence, then one sheet; links are named for a reader, and nothing carries a native tooltip.
  assert.equal(section.querySelector(".say").textContent,
    "These plans are being reviewed by agents before any delivery takes them up; each has its own live view.");
  assert.equal(section.querySelectorAll(".preview-sheet").length, 1);
  assert.deepEqual([...section.querySelectorAll("a")].map((link) => link.getAttribute("aria-label")),
    ["Live view of Plan A", "Hosted view of Plan A", "Live view of b-slug"]);
  assert.equal(section.querySelector("[title]"), null);
  // The runs are listed as without a preview.
  assert.deepEqual([...shown.app.querySelectorAll("#now [data-run]")].map((node) => node.getAttribute("data-run")),
    [INSIDE_PLAN, EXECUTE_AGAIN, REVIEW_AGAIN, AGENT]);
});

test("a runs event that brings a preview draws Plans in review without a reload", async () => {
  const result = await openPage(served(PAGE), { server: { document: {} }, rendered: served(renderWithPreviews(PREVIEWS.slice(0, 1))),
    address: "http://localhost/", fetchAt: "/view/runs.html", steps: [{ runs: RUNS_EVENT, wait: 50 }] });
  const [before, after] = result;
  assert.equal(before.app.querySelector("#previews"), null);
  assert.deepEqual(previewRows(after.app).map((row) => row.name), ["Plan A"]);
  assert.equal(after.app.querySelector("#previews .say").textContent,
    "A plan is being reviewed by agents before any delivery takes it up; it has its own live view.");
  assert.equal(result.navigations.length, 0);
});

test("a preview's title reads as the line map's prose: a quoted name is code", async () => {
  const [shown] = await openPage(renderWithPreviews([{ live: "127.0.0.1:4102", plan: "c.md", title: "Serve `plan-view` links" }]));
  const title = shown.app.querySelector("#previews .preview-name b");
  assert.equal(title.textContent, "Serve plan-view links");
  assert.equal(title.querySelector("code.prose-code").textContent, "plan-view");
  assert.equal(title.querySelector("a[aria-label]"), null);
  assert.equal(shown.app.querySelector("#previews .preview-live").getAttribute("aria-label"), "Live view of Serve plan-view links");
});

// The runs page under the page controller (page.js): it draws through page.redraw, so a push or a runs event keeps the
// reader's place, open rows and focus, and its rows open through page.fold, keyed by run.
const listed = JSON.parse(COMMANDS["runs list --json"]);
/** The page before INSIDE_PLAN was listed: the run a push then adds sits above every ended run. */
const FEWER = renderRunsPage(ABSENT, { runsCommands: { ...COMMANDS,
  "runs list --json": JSON.stringify({ ...listed, runs: listed.runs.filter((run) => run.runId !== INSIDE_PLAN) }) } });
const live = (page, steps, scenario = {}) => openPage(served(page), { server: { document: {} }, rendered: served(PAGE),
  address: "http://localhost/", fetchAt: "/view/runs.html", ...scenario, steps });
const headOf = (window, runId) => window.document.querySelector(`[data-run="${runId}"] > .run-head`);
/** Where the run's head stands in the window, and the window's scroll. */
const standing = (window, runId) => ({ top: headOf(window, runId).getBoundingClientRect().top, y: window.scrollY });

test("a push and a runs event leave the reader's place, and a run added above leaves the run they were at where it stood", async () => {
  const seen = [];
  const at = (label) => ({ run(window) { seen.push([label, standing(window, ENDED)]); } });
  const scrolled = { run(window) { window.scrollTo({ top: window.scrollY + headOf(window, ENDED).getBoundingClientRect().top - 40, behavior: "instant" }); } };
  await live(PAGE, [scrolled, at("scrolled"), { event: { state: {} }, wait: 50 }, at("pushed"), { runs: RUNS_EVENT, wait: 50 }, at("runs")],
    { layout: { height: 120 } });
  const [[, before]] = seen;
  expect(seen).toEqual([["scrolled", before], ["pushed", before], ["runs", before]]);
  const added = [];
  await live(FEWER, [scrolled, { run(window) { added.push(standing(window, ENDED)); } }, { event: { state: {} }, wait: 50 },
    { run(window) { added.push(standing(window, ENDED), window.document.querySelectorAll("#now [data-run]").length); } }],
  { layout: { height: 120 } });
  expect([added[1].top, added[2]]).toEqual([added[0].top, 4]);
  expect(added[1].y > added[0].y).toBe(true);
});

test("focus on an ended run's head stays on that run when a push adds a run above it, and two open rows stay open", async () => {
  const seen = [];
  await live(FEWER, [{ click: `[data-run="${LEFT_OPEN}"] > .run-head` }, { click: `[data-run="${ENDED}"] > .run-head` },
    { event: { state: {} }, wait: 50 },
    { run(window) {
      const active = window.document.activeElement;
      seen.push(active.closest("[data-run]")?.getAttribute("data-run"), active.getAttribute("data-key"),
        [LEFT_OPEN, ENDED].map((runId) => headOf(window, runId).getAttribute("aria-expanded")),
        window.document.querySelectorAll("#now [data-run]").length);
    } }]);
  expect(seen).toEqual([ENDED, `fold:run:${ENDED}`, ["true", "true"], 4]);
});

test("a deep link to Ended or to a run opens there through the controller, and the run's panel opens", async () => {
  for (const [name, opens] of [["ended", null], [ENDED, ENDED]]) {
    const [shown] = await openPage(PAGE, { rendered: PAGE, address: `http://localhost/view/runs.html#${name}`, layout: { height: 120 },
      steps: [{ run(window) {
        const box = window.document.getElementById(name).getBoundingClientRect();
        expect([name, window.scrollY > 0, box.top >= 0 && box.top < 120]).toEqual([name, true, true]);
      } }] });
    expect(shown.app.querySelectorAll('.run-head[aria-expanded="true"]').length).toBe(opens ? 1 : 0);
    if (opens) expect(runNode(shown.app, opens).querySelector(".run-detail").hasAttribute("hidden")).toBe(false);
  }
});

test("links away from the runs page are left to the browser", async () => {
  const kept = [];
  await openPage(renderWithPreviews(PREVIEWS), { steps: [{ run(window) {
    for (const link of window.document.querySelectorAll(".kicker a, #previews a")) {
      // The window hears the click last: what the page did with it is read there, then the leaving jsdom can't do is stopped.
      window.addEventListener("click", (event) => { if (event.defaultPrevented) kept.push(link.textContent); event.preventDefault(); }, { once: true });
      link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
    }
    kept.push(window.document.querySelectorAll(".kicker a, #previews a").length);
  } }] });
  expect(kept).toEqual([5]);
});

const FOCUSABLE = "#app :is(a[href], button, summary, input, select, [tabindex])";
test("every focusable control on the runs page, with previews and every run open, carries a key", async () => {
  const missing = [];
  await openPage(renderWithPreviews(PREVIEWS), { steps: [{ run(window) {
    for (const head of window.document.querySelectorAll(".run-head")) head.click();
    for (const node of window.document.querySelectorAll(FOCUSABLE)) {
      if (!node.getAttribute("data-key")) missing.push(`${node.tagName.toLowerCase()}.${(node.getAttribute("class") || "").split(" ")[0]}`);
    }
  } }] });
  expect(missing).toEqual([]);
});

/** Each line of `runs.js` and `history.js` that does what `pattern` finds, as "file:line". */
function found(pattern, sources) {
  return Object.entries(sources).flatMap(([name, source]) => source.split("\n").flatMap((line, index) =>
    !/^\s*\/\//.test(line) && pattern.test(line) ? [`${name}:${index + 1}`] : []));
}
const PAGES = Object.fromEntries(["runs.js", "history.js"].map((name) => [name, readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", name), "utf8")]));
const DISCLOSES = /\.hidden\s*=[^=]|aria-expanded|["']hidden["']/;
const STORES = /localStorage|sessionStorage/;

test("the runs and history pages open and shut their rows only through page.fold, and read no storage", () => {
  expect([found(DISCLOSES, PAGES), found(STORES, PAGES)]).toEqual([[], []]);
  for (const planted of ["detail.hidden = true;", 'button.setAttribute("aria-expanded", "true");', 'detail.removeAttribute("hidden");']) {
    expect([planted, found(DISCLOSES, { ...PAGES, "runs.js": `${PAGES["runs.js"]}\n${planted}` }).length]).toEqual([planted, 1]);
  }
  expect(found(STORES, { ...PAGES, "history.js": `${PAGES["history.js"]}\nlocalStorage.getItem("x");` }).length).toBe(1);
});
