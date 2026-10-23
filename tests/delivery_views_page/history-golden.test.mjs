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
