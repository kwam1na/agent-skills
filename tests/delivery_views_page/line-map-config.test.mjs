// What the configuration and the round record add to the line map: briefs and results on the round card,
// configured names and icons, the theme key, and no run section without a runs command.
import { test } from "node:test";
import { REPLAYED_RUNS, block, expect, openPage, pageWithRuns } from "./support.mjs";

process.env.TZ = "UTC";

const NOW = "2026-10-22T05:10:00Z";
const HOSTILE = "</script><img src=x onerror=alert(1)> <b>bold</b> & \"quoted\" <!--<script>";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS,
  lenses: { "lens.outcome-correctness": { name: "Correctness" }, "lens.adversarial-testing": { name: "Testing" } } };

function record(runId = "run-1") {
  return { state: {
    meta: { headline: "Payments" },
    lanes: [{ id: "lane-pay", title: "Payments", state: "working" }],
    queue: [{ subject: "PAY-1", title: "Retry card payments", lane: "lane-pay", stage: "checking", order: 1, runId }],
  }, journal: [] };
}

function run(briefs, raised = [{ id: "r1-f1", lens: "lens.adversarial-testing", severity: "P2", summary: "A retry charges twice.", state: "unresolved", deferredIssue: null }]) {
  return { "run-1": { runId: "run-1", ticket: null, open: true, status: "incomplete", startedAt: null, lastAt: null, durationSeconds: null,
    rounds: 1, findings: null, roundDetail: [{ round: 1, at: "2026-10-22T04:40:00Z", outcome: "changes", findings: { P0: 0, P1: 0, P2: 1, P3: 0 },
      verdicts: { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "changes" }, raised, settled: [], ...(briefs ? { briefs } : {}) }] } };
}

const card = (snapshot) => snapshot.app.querySelector("#detail .run-card");
const reviewer = (snapshot, name) => [...card(snapshot).querySelectorAll(".run-reviewer")]
  .find((group) => group.querySelector(".run-lens-name")?.textContent === name);

test("each reviewer's brief and result expand in place, and a missing one says so quietly", async () => {
  const briefs = {
    "lens.adversarial-testing": { brief: { text: "Try to break the retry.", shortened: false }, result: { text: "Found a double charge.", shortened: true } },
    "lens.outcome-correctness": { brief: null, result: { unavailable: true } },
  };
  const page = pageWithRuns(record(), run(briefs), { config: CONFIG });
  const [first, opened, both, closed] = await openPage(page, { now: NOW, steps: [
    { click: "#detail .run-reviewer .run-record-pick" },
    { click: "#detail .run-reviewer .run-record-pick", index: 1 },
    { click: "#detail .run-reviewer .run-record-pick" },
  ] });
  const testing = reviewer(first, "Testing");
  expect([...testing.querySelectorAll(".run-record-pick")].map((pick) => [pick.textContent, pick.getAttribute("aria-expanded")]))
    .toEqual([["Brief", "false"], ["Result", "false"]]);
  expect(testing.querySelector(".run-record-text")).toBeNull();
  expect(reviewer(opened, "Testing").querySelector(".run-record-text.is-brief pre").textContent).toBe("Try to break the retry.");
  const result = reviewer(both, "Testing").querySelector(".run-record-text.is-result");
  expect(result.querySelector("pre").textContent).toBe("Found a double charge.");
  expect(result.textContent).toContain("Shortened: the first 64 KiB are shown.");
  // A second click folds the text away again.
  expect(reviewer(closed, "Testing").querySelector(".run-record-text.is-brief")).toBeNull();
  expect(reviewer(first, "Correctness").querySelector(".run-record-row").textContent).toBe("no brief recordedResult unavailable");
});

test("a round whose report records no briefs shows neither brief nor result", async () => {
  const [first] = await openPage(pageWithRuns(record(), run(null), { config: CONFIG }), { now: NOW });
  expect(card(first).querySelector(".run-record")).toBeNull();
  expect(card(first).textContent).not.toContain("no brief recorded");
});

test("a lens with a brief but no findings or verdict is listed, and a quiet round names each lens's record", async () => {
  const briefs = { "lens.security": { brief: { text: "Look for leaks.", shortened: false }, result: null } };
  const [first] = await openPage(pageWithRuns(record(), run(briefs), { config: CONFIG }), { now: NOW });
  expect(reviewer(first, "Security").querySelector(".run-record-row").textContent).toBe("Briefno result recorded");
  const [quiet] = await openPage(pageWithRuns(record(), run(briefs, []), { config: CONFIG }), { now: NOW });
  expect(card(quiet).querySelector(".run-record-lens").textContent).toBe("Security");
});

test("hostile brief and result text, and hostile lens keys, stay text", async () => {
  const briefs = { [HOSTILE]: { brief: { text: HOSTILE, shortened: false }, result: { text: HOSTILE, shortened: false } },
    "__proto__": { brief: { text: "proto", shortened: false }, result: null } };
  const page = pageWithRuns(record(), run(briefs), { config: CONFIG });
  expect(page).not.toContain("</script><img");
  const snaps = await openPage(page, { now: NOW, steps: [{ click: "#detail .run-record-pick" }, { click: "#detail .run-record-pick", index: 1 }] });
  const shown = card(snaps[2]);
  expect(shown.querySelector("img")).toBeNull();
  expect([...shown.querySelectorAll("b")].filter((node) => node.textContent === "bold")).toHaveLength(0);
  expect([...shown.querySelectorAll(".run-record-text pre")].map((pre) => pre.textContent)).toContain(HOSTILE);
  // An unconfigured lens is named by its id's last word, as text.
  expect([...shown.querySelectorAll(".run-lens-name")].map((name) => name.textContent)).toContain("<script>");
});

test("lens names and icons come from the configuration, and an unconfigured lens shows its initial", async () => {
  const config = { ...CONFIG, lenses: { "lens.adversarial-testing": { name: "Red team", icon: "shield" }, "lens.outcome-correctness": { name: HOSTILE.slice(0, 40) } } };
  const [first] = await openPage(pageWithRuns(record(), run(null, [
    { id: "r1-f1", lens: "lens.adversarial-testing", severity: "P2", summary: "x.", state: "unresolved", deferredIssue: null },
    { id: "r1-f2", lens: "lens.perf", severity: "P3", summary: "y.", state: "unresolved", deferredIssue: null }]), { config }), { now: NOW });
  const names = [...card(first).querySelectorAll(".run-lens-name")].map((name) => name.textContent);
  expect(names).toEqual(["Red team", HOSTILE.slice(0, 40), "Perf"]);
  expect(card(first).querySelector("img")).toBeNull();
  const icons = [...card(first).querySelectorAll(".run-reviewer-head .run-lens-icon")];
  expect(icons[0].querySelector("path").getAttribute("d")).toMatch(/^M12 3a12 12/);
  expect(icons[2].querySelector(".run-lens-initial").textContent).toBe("P");
});

test("a hostile title stays text in the page's title and headline", async () => {
  const title = "<img src=x onerror=alert(1)> & co";
  const page = pageWithRuns({ state: { queue: [] }, journal: [] }, {}, { config: { ...CONFIG, title } });
  expect(page).toContain("<title>&lt;img src=x onerror=alert(1)&gt; &amp; co</title>");
  const [first] = await openPage(page, { now: NOW });
  expect(first.app.querySelector("#now .kicker .headline").textContent).toBe(title);
  expect(first.app.querySelector("img")).toBeNull();
});

test("the theme is kept under the configured key, so two repositories never share it", async () => {
  const one = pageWithRuns(record(), {}, { config: CONFIG, themeKey: "delivery-views:aaaa:theme" });
  const two = pageWithRuns(record(), {}, { config: CONFIG, themeKey: "delivery-views:bbbb:theme" });
  expect(block(one, "delivery-view").themeKey).not.toBe(block(two, "delivery-view").themeKey);
  const picked = await openPage(one, { now: NOW, rendered: one, steps: [{ click: '[data-theme-pick="dark"]' }] });
  expect(picked.storage).toEqual({ "delivery-views:aaaa:theme": "dark" });
});

test("with no runs command configured, a stop that names a run shows no run section", async () => {
  const page = pageWithRuns(record(), {}, { config: { spec: "delivery-views-config/1" } });
  const [first] = await openPage(page, { now: NOW });
  expect(first.app.querySelector("#detail .d-run")).toBeNull();
  expect(first.app.querySelector("#detail .d-run-none")).toBeNull();
  // With one configured, the same stop says its run could not be read.
  const [configured] = await openPage(pageWithRuns(record(), {}, { config: CONFIG }), { now: NOW });
  expect(configured.app.querySelector("#detail .d-run-none").textContent).toBe("Run run-1 unavailable.");
});

test("the history link opens the configured history page, live and as a file, and is absent without one", async () => {
  const page = pageWithRuns(record(), {}, { config: CONFIG, history: "delivery-history.html" });
  const [file] = await openPage(page, { now: NOW });
  expect(file.app.querySelector(".history-link").getAttribute("href")).toBe("delivery-history.html");
  const [live] = await openPage(page, { now: NOW, rendered: page });
  expect(live.app.querySelector(".history-link").getAttribute("href")).toBe("/view/delivery-history.html");
  const [none] = await openPage(pageWithRuns(record(), {}, { config: CONFIG }), { now: NOW });
  expect(none.app.querySelector(".history-link")).toBeNull();
});

test("finding ids and lenses named like built-in properties draw as any other", async () => {
  const raised = ["__proto__", "constructor", "toString"].map((id, index) => ({ id, lens: id === "toString" ? "constructor" : "toString",
    severity: "P2", summary: `Finding ${index}.`, state: "resolved", deferredIssue: null }));
  const runs = run(null, raised);
  runs["run-1"].roundDetail.push({ round: 2, at: "2026-10-22T04:58:00Z", outcome: "approve", findings: { P0: 0, P1: 0, P2: 0, P3: 0 },
    verdicts: {}, raised: [], settled: [{ id: "constructor", state: "fixed", fixCommit: null, deferredIssue: null }] });
  const page = pageWithRuns(record(), runs, { config: CONFIG });
  const [, first] = await openPage(page, { now: NOW, steps: [{ click: "#detail .run-pick" }] });
  const shown = card(first);
  expect([...shown.querySelectorAll(".run-finding-text")].map((text) => text.textContent)).toEqual(["Finding 2.", "Finding 0.", "Finding 1."]);
  expect(first.text).not.toContain("native code");
  expect(shown.textContent).toContain("1 of 3 fixed");
});

test("a history output in a folder keeps its slash in the link", async () => {
  const page = pageWithRuns(record(), {}, { config: CONFIG, history: "views/delivery history.html" });
  const [live] = await openPage(page, { now: NOW, rendered: page });
  expect(live.app.querySelector(".history-link").getAttribute("href")).toBe("/view/views/delivery%20history.html");
});
