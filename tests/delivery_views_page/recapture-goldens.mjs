// Re-derives the line map's golden outputs from the page as it now stands, with each case's clock, stops and folds kept
// as its golden names them: `node tests/delivery_views_page/recapture-goldens.mjs`. A golden changes only by a run of
// this, committed apart from any page change, so the diff shows what the page now draws and nothing else.
import { readdirSync, existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { CORPUS, ROOT, block, openPage, pageText, pageWithRuns, readJson, renderPage } from "./support.mjs";

process.env.TZ = "UTC";

const write = (file, value) => writeFileSync(file, JSON.stringify(value, null, 2) + "\n");
const CONFIG = readJson(path.join(CORPUS, "linked-config.json"));

for (const name of readdirSync(CORPUS).filter((entry) => existsSync(path.join(CORPUS, entry, "golden.json"))).sort()) {
  const folder = path.join(CORPUS, name);
  const golden = readJson(path.join(folder, "golden.json"));
  const planFile = path.join(folder, "plan.json");
  const page = renderPage(readJson(path.join(folder, "document.json")), { config: CONFIG,
    runsCommands: readJson(path.join(folder, "runs-commands.json")), plan: existsSync(planFile) ? readJson(planFile) : null });
  // The blocks the golden test compares; the links block stays as the source design captured it, which no row reads.
  for (const id of ["delivery-state", "delivery-runs", "delivery-plan"]) if (id in golden.blocks) golden.blocks[id] = block(page, id);
  const [first] = await openPage(page, { now: golden.now });
  golden.initialText = pageText(first.app);
  for (const fold of Object.keys(golden.folds ?? {})) {
    const selector = `.plan-fold[data-fold="${fold}"]`;
    const [, opened] = await openPage(page, { now: golden.now, steps: [{ toggle: selector }] });
    golden.folds[fold] = opened.app.querySelector(selector).textContent;
  }
  for (const subject of Object.keys(golden.details)) {
    const stop = `:is(.stop-btn, .mg-tick)[data-subject="${subject}"]`;
    const [, shown] = await openPage(page, { now: golden.now, steps: [{ click: stop }] });
    const picks = shown.app.querySelectorAll("#detail .run-pick").length;
    const entry = { detail: shown.app.querySelector("#detail").textContent };
    if (picks) {
      const snaps = await openPage(page, { now: golden.now,
        steps: [{ click: stop }, ...Array.from({ length: picks }, (_, index) => ({ click: "#detail .run-pick", index }))] });
      entry.rounds = snaps.slice(2).map((snap) => snap.app.querySelector("#detail .run-card")?.textContent ?? "");
    }
    golden.details[subject] = entry;
  }
  write(path.join(folder, "golden.json"), golden);
}

const linked = path.join(CORPUS, "parked-round-reports");
write(path.join(CORPUS, "linked-links.json"), block(renderPage(readJson(path.join(linked, "document.json")),
  { config: CONFIG, runsCommands: readJson(path.join(linked, "runs-commands.json")) }), "delivery-links"));

const deferred = path.join(ROOT, "tests", "fixtures", "delivery-views", "round-card-deferred");
const document = readJson(path.join(deferred, "document.json"));
const picks = document.reviewEvidence["WORK-1"].outcomes.length;
const snaps = await openPage(pageWithRuns(document, {}, { config: { spec: "delivery-views-config/1" } }),
  { steps: Array.from({ length: picks }, (_, index) => ({ click: ".run-pick", index })) });
write(path.join(deferred, "golden.json"), { ...readJson(path.join(deferred, "golden.json")),
  rounds: snaps.slice(1).map((snap) => snap.app.querySelector("#detail .run-card").textContent) });
