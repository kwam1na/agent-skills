// The runs page, rendered by the shipped renderer from the runtime's captured output (tests/fixtures/run-events/runs) and opened as a browser would.
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { RUNS_CORPUS, block, openPage, readJson, renderRunsPage, served } from "./support.mjs";

process.env.TZ = "UTC";

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
  const [shown] = await openPage(PAGE);
  const captured = { block: block(PAGE, "delivery-run-page"), view: block(PAGE, "delivery-run-page-view"), text: shown.text };
  if (process.env.RECAPTURE_RUNS_GOLDEN === "1" || !existsSync(golden)) {
    writeFileSync(golden, JSON.stringify(captured, null, 2) + "\n");
    assert.fail("captured the runs golden; check it in and run again");
  }
  assert.deepEqual(captured, readJson(golden));
});
