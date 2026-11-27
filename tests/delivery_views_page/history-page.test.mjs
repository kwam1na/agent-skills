// The history page under the page controller (page.js): a first load at a row's id opens that row, filled with its run
// summary, and scrolls it into view once; every control it draws carries a key.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { HISTORY_CORPUS, expect, openPage, readJson, renderHistoryPage } from "./support.mjs";

process.env.TZ = "UTC";

const CASES = readdirSync(HISTORY_CORPUS).filter((name) => existsSync(path.join(HISTORY_CORPUS, name, "golden.json"))).sort();
const CONFIG = readJson(path.join(HISTORY_CORPUS, "history-config.json"));

function rendered(name) {
  const folder = path.join(HISTORY_CORPUS, name);
  const golden = readJson(path.join(folder, "golden.json"));
  const directory = path.join(folder, "exports");
  const exports = existsSync(directory)
    ? Object.fromEntries(readdirSync(directory).map((file) => [file, readFileSync(path.join(directory, file), "utf8")])) : {};
  const page = renderHistoryPage(readJson(path.join(folder, "document.json")), {
    config: CONFIG, runsCommands: readJson(path.join(folder, "runs-commands.json")), exports, now: golden.asOf,
  });
  return { golden, page };
}
const PAGES = Object.fromEntries(CASES.map((name) => [name, rendered(name)]));

test("a first load at a row's id opens that row with its run summary and scrolls it into view, once", async () => {
  const { golden, page } = PAGES.overlap;
  const key = golden.rows[2];
  const seen = [];
  const shown = await openPage(page, { now: golden.now, rendered: page, address: `http://localhost/view/delivery-history.html#${key}`,
    layout: { height: 200 }, steps: [{ run(window) {
      const box = window.document.getElementById(key).getBoundingClientRect();
      seen.push(window.scrollY > 0, box.top >= 0 && box.top < 200, window.scrollY);
    } }, { wait: 20 }, { run(window) { seen.push(window.scrollY); } },
    // Shut and opened again, the row shows its one summary: it is drawn the first time only.
    { click: `[id="${key}"] > button` }, { click: `[id="${key}"] > button` }] });
  const row = shown[0].app.querySelector(`[id="${key}"]`);
  expect([row.getAttribute("data-run"), row.querySelector("button").getAttribute("aria-expanded"), row.nextElementSibling.textContent])
    .toEqual([key, "true", golden.details[key].detail]);
  expect(shown[0].app.querySelectorAll('[data-run] > button[aria-expanded="true"]').length).toBe(1);
  expect(shown.at(-1).app.querySelector(`[id="${key}"]`).nextElementSibling.textContent).toBe(golden.details[key].detail);
  // Placed once: nothing moves it after.
  expect([seen[0], seen[1], seen[3]]).toEqual([true, true, seen[2]]);
});

const FOCUSABLE = "#app :is(a[href], button, summary, input, select, [tabindex])";
test("every focusable control on every history golden case, with every row open, carries a key", async () => {
  const missing = [];
  for (const [name, { golden, page }] of Object.entries(PAGES)) {
    await openPage(page, { now: golden.now, steps: [{ run(window) {
      for (const button of window.document.querySelectorAll("[data-run] > button")) button.click();
      for (const node of window.document.querySelectorAll(FOCUSABLE)) {
        if (!node.getAttribute("data-key")) missing.push(`${name}: ${node.tagName.toLowerCase()}.${(node.getAttribute("class") || "").split(" ")[0]}`);
      }
    } }] });
  }
  expect(missing).toEqual([]);
});
