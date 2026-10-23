// The history page equals the golden outputs captured from its source design, case by case.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { HISTORY_CORPUS, block, openPage, readJson, renderHistoryPage } from "./support.mjs";

process.env.TZ = "UTC";

const CASES = readdirSync(HISTORY_CORPUS).filter((name) => existsSync(path.join(HISTORY_CORPUS, name, "golden.json"))).sort();
const CONFIG = readJson(path.join(HISTORY_CORPUS, "history-config.json"));

function exportsOf(folder) {
  const directory = path.join(folder, "exports");
  return existsSync(directory)
    ? Object.fromEntries(readdirSync(directory).map((file) => [file, readFileSync(path.join(directory, file), "utf8")]))
    : {};
}

function rendered(name) {
  const folder = path.join(HISTORY_CORPUS, name);
  const golden = readJson(path.join(folder, "golden.json"));
  const page = renderHistoryPage(readJson(path.join(folder, "document.json")), {
    config: CONFIG, runsCommands: readJson(path.join(folder, "runs-commands.json")), exports: exportsOf(folder), now: golden.asOf,
  });
  return { golden, page };
}

test("the corpus holds every captured case", () => {
  assert.deepEqual(CASES, ["delivery-records-epic", "empty", "hostile-text", "overlap", "run-list-unreadable", "unreadable-rows"]);
});

for (const name of CASES) {
  test(`${name}: the embedded history, the page text and every row's detail equal the golden`, async () => {
    const { golden, page } = rendered(name);
    assert.deepEqual(block(page, "delivery-history"), golden.blocks["delivery-history"]);
    const [first] = await openPage(page, { now: golden.now });
    assert.equal(first.text, golden.initialText);
    assert.deepEqual([...first.app.querySelectorAll("[data-run]")].map((row) => row.getAttribute("data-run")), golden.rows);
    for (const [index, key] of golden.rows.entries()) {
      const button = "[data-run] > button";
      const [, opened, closed] = await openPage(page, { now: golden.now, steps: [{ click: button, index }, { click: button, index }] });
      assert.equal(opened.app.querySelectorAll("[data-run]")[index].nextElementSibling.textContent, golden.details[key].detail, key);
      assert.equal(closed.app.querySelectorAll("[data-run]")[index].nextElementSibling.hasAttribute("hidden"), golden.details[key].closed, key);
    }
  });
}

// The source linked its own file name and the live server's front page; the port links the binding's
// line-map renderer, as a file and at its /view/ address, so these differ from the golden by design.
test("the line map link is the binding's line-map output as a file and its /view/ address when served", async () => {
  const { page } = rendered("overlap");
  const [file] = await openPage(page, { store: null });
  const [served] = await openPage(page, { store: null, rendered: page });
  assert.deepEqual([...file.app.querySelectorAll(".kicker a")].map((link) => link.getAttribute("href")), ["line-map.html"]);
  assert.deepEqual([...served.app.querySelectorAll(".kicker a")].map((link) => link.getAttribute("href")), ["/view/line-map.html"]);
});

test("a line map output in a folder keeps its separators, as a file and when served", async () => {
  const renderers = [
    { id: "line-map", argv: ["python3", "-B", "render.py", "line-map"], output: "views/line map.html", timeoutSeconds: 30 },
    { id: "history", argv: ["python3", "-B", "render.py", "history"], output: "views/history.html", timeoutSeconds: 60 },
  ];
  const page = renderHistoryPage({}, { config: { spec: "delivery-views-config/1" }, renderers });
  const [file] = await openPage(page);
  const [served] = await openPage(page, { rendered: page });
  assert.equal(file.app.querySelector(".kicker a").getAttribute("href"), "views/line%20map.html");
  assert.equal(served.app.querySelector(".kicker a").getAttribute("href"), "/view/views/line%20map.html");
});

test("the page names only the sources the configuration gives it", async () => {
  const say = async (config) => (await openPage(renderHistoryPage({}, { config, now: "2026-10-22T12:00:00Z" })))[0].app.querySelector(".say").textContent;
  const runs = { argv: ["python3", "-c", "print('[]')"] };
  const exports = { directory: "exported" };
  assert.equal(await say({ spec: "delivery-views-config/1", runs, exports }),
    "Runs from the delivery product and the exports tracked under exported, as of 2026-10-22 12:00 UTC. Reload for newer.");
  assert.equal(await say({ spec: "delivery-views-config/1", runs }), "Runs from the delivery product, as of 2026-10-22 12:00 UTC. Reload for newer.");
  assert.equal(await say({ spec: "delivery-views-config/1", exports }),
    "Runs from the exports tracked under exported, as of 2026-10-22 12:00 UTC. Reload for newer.");
  assert.equal(await say({ spec: "delivery-views-config/1" }), "No runs command or export directory is configured.");
});
