// The line map equals the golden outputs captured from its source design, case by case. A case with a plan.json renders
// with it in the state directory and also pins the plan chapter's folds; a case without one embeds no plan at all.
import assert from "node:assert/strict";
import { readdirSync, existsSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { CORPUS, block, openPage, readJson, renderPage } from "./support.mjs";

process.env.TZ = "UTC";

const CASES = readdirSync(CORPUS).filter((name) => existsSync(path.join(CORPUS, name, "golden.json"))).sort();
const CONFIG = readJson(path.join(CORPUS, "linked-config.json"));

test("the corpus holds every captured case", () => {
  assert.deepEqual(CASES, ["delivery-records-epic", "parked-no-runs", "parked-round-report-unreadable", "parked-round-reports",
    "parked-run-view", "plan-in-app", "plan-record-only", "round-card-record"]);
});

for (const name of CASES) {
  test(`${name}: the embedded record and runs, the page text and every detail panel equal the golden`, async () => {
    const folder = path.join(CORPUS, name);
    const golden = readJson(path.join(folder, "golden.json"));
    const document = readJson(path.join(folder, "document.json"));
    const planFile = path.join(folder, "plan.json");
    const plan = existsSync(planFile) ? readJson(planFile) : null;
    const page = renderPage(document, { config: CONFIG, runsCommands: readJson(path.join(folder, "runs-commands.json")), plan });
    assert.deepEqual(block(page, "delivery-state"), golden.blocks["delivery-state"]);
    assert.deepEqual(block(page, "delivery-runs"), golden.blocks["delivery-runs"]);
    assert.deepEqual(block(page, "delivery-plan"), golden.blocks["delivery-plan"] ?? null);
    const [first] = await openPage(page, { now: golden.now });
    assert.equal(first.text, golden.initialText);
    for (const [name, expected] of Object.entries(golden.folds ?? {})) {
      const fold = `.plan-fold[data-fold="${name}"]`;
      const [, opened] = await openPage(page, { now: golden.now, steps: [{ toggle: fold }] });
      assert.equal(opened.app.querySelector(fold).textContent, expected, name);
    }
    for (const [subject, expected] of Object.entries(golden.details)) {
      const stop = `.stop-btn[data-subject="${subject}"]`;
      const [, shown] = await openPage(page, { now: golden.now, steps: [{ click: stop }] });
      assert.equal(shown.app.querySelector("#detail").textContent, expected.detail, subject);
      const picks = shown.app.querySelectorAll("#detail .run-pick").length;
      assert.equal(picks, expected.rounds ? expected.rounds.length : 0, subject);
      if (!picks) continue;
      const snaps = await openPage(page, { now: golden.now,
        steps: [{ click: stop }, ...Array.from({ length: picks }, (_, index) => ({ click: "#detail .run-pick", index }))] });
      assert.deepEqual(snaps.slice(2).map((snap) => snap.app.querySelector("#detail .run-card")?.textContent ?? ""), expected.rounds, subject);
    }
  });
}

test("the link block holds the configured tracker's and forge's addresses for the record's ids", () => {
  const folder = path.join(CORPUS, "parked-round-reports");
  const page = renderPage(readJson(path.join(folder, "document.json")),
    { config: CONFIG, runsCommands: readJson(path.join(folder, "runs-commands.json")) });
  assert.deepEqual(block(page, "delivery-links"), readJson(path.join(CORPUS, "linked-links.json")));
});
