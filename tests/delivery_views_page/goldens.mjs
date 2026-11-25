// The line map's golden outputs as the page now draws them, each case's clock, stops and folds kept as its golden names
// them: every file recapture-goldens.mjs writes, by its path, with the text it writes. A golden test holds the corpus
// to them, so a change to the page that moves any golden fails until its recapture is committed.
import { readdirSync, existsSync } from "node:fs";
import path from "node:path";
import { CORPUS, ROOT, block, openPage, pageText, pageWithRuns, readJson, renderPage } from "./support.mjs";

const serialized = (value) => JSON.stringify(value, null, 2) + "\n";

/** Every golden file of the line map, recaptured: {path: text}. */
export async function recapturedGoldens() {
  const files = {};
  const config = readJson(path.join(CORPUS, "linked-config.json"));
  for (const name of readdirSync(CORPUS).filter((entry) => existsSync(path.join(CORPUS, entry, "golden.json"))).sort()) {
    const folder = path.join(CORPUS, name);
    const golden = readJson(path.join(folder, "golden.json"));
    const planFile = path.join(folder, "plan.json");
    const page = renderPage(readJson(path.join(folder, "document.json")), { config,
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
    for (const [id, selector] of Object.entries(golden.chapters ?? {}).map(([id]) => [id, `#${id}`])) {
      const [, shown] = await openPage(page, { now: golden.now, steps: [{ click: `.rail a[href="#${id}"]` }] });
      golden.chapters[id] = shown.app.querySelector(selector).textContent;
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
    files[path.join(folder, "golden.json")] = serialized(golden);
  }

  const linked = path.join(CORPUS, "parked-round-reports");
  files[path.join(CORPUS, "linked-links.json")] = serialized(block(renderPage(readJson(path.join(linked, "document.json")),
    { config, runsCommands: readJson(path.join(linked, "runs-commands.json")) }), "delivery-links"));

  const deferred = path.join(ROOT, "tests", "fixtures", "delivery-views", "round-card-deferred");
  const document = readJson(path.join(deferred, "document.json"));
  const picks = document.reviewEvidence["WORK-1"].outcomes.length;
  const snaps = await openPage(pageWithRuns(document, {}, { config: { spec: "delivery-views-config/1" } }),
    { steps: Array.from({ length: picks }, (_, index) => ({ click: ".run-pick", index })) });
  files[path.join(deferred, "golden.json")] = serialized({ ...readJson(path.join(deferred, "golden.json")),
    rounds: snaps.slice(1).map((snap) => snap.app.querySelector("#detail .run-card").textContent) });
  return files;
}
