import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { build } from "esbuild";
const root = new URL("../../../", import.meta.url);
const corpus = (name) =>
  readFile(new URL(`skills/delivery-views/assets/${name}`, root), "utf8");
let line = await corpus("line-map.js");
function patch(before, after) {
  if (line.split(before).length !== 2) throw Error("hosted.asset_hook_drift");
  line = line.replace(before, after);
}
// Small host adapter; every chapter/detail renderer still comes from the corpus.
// The hosted snapshot carries no story and no entry's facts, by its schema. The page says what is not published rather
// than that nothing was recorded, and leaves out the steps and notes it would draw without words.
patch(
  "  function runFor(doc, item) {",
  'function hostedCut(doc) { return !!(globalThis.deliveryStateStore && typeof globalThis.deliveryStateStore.projection === "function") && !isObject(doc.story); }\n  var UNPUBLISHED = "Its reason is not published on the hosted page.";\n  function runFor(doc, item) {',
);
patch(
  'el("h2", "title", "The story could not be told from this record.")',
  'el("h2", "title", hostedCut(doc) ? "What happened is not published on the hosted page." : "The story could not be told from this record.")',
);
// A kept review publishes no facts: the publisher matched its digest and candidate to the native review it sent.
patch(
  "          || (evidence && (!keptReview(evidence) || keptReview(evidence).digest !== native.digest))",
  "          || (!hostedCut(doc) && evidence && (!keptReview(evidence) || keptReview(evidence).digest !== native.digest))",
);
patch(
  "    return evidence && keptReview(evidence) ? keptReview(evidence).candidateRef : undefined;",
  "    if (hostedCut(doc)) { var sent = nativeReviews[item.subject]; return isObject(sent) ? sent.candidateRef : undefined; }\n    return evidence && keptReview(evidence) ? keptReview(evidence).candidateRef : undefined;",
);
// A stopped piece's and a broken base's reasons are not patched: the page draws them only from the supervisor's
// published needs list, already in words, and from a closed page's left-open block, which the hosted page never carries.
patch(
  'toldLine(doc, events[events.length - 1]) || "No reason recorded."',
  'toldLine(doc, events[events.length - 1]) || (hostedCut(doc) ? UNPUBLISHED : "No reason recorded.")',
);
patch(
  "    var notes = list(doc.journal).filter(function (entry) {\n",
  "    var notes = list(doc.journal).filter(function (entry) {\n      if (hostedCut(doc)) { return false; }\n",
);
patch(
  "    var rows = journalFor(doc, item.subject, HISTORY);\n    if (!rows.length) { return null; }",
  "    var rows = journalFor(doc, item.subject, HISTORY);\n    if (!rows.length || hostedCut(doc)) { return null; }",
);
patch(
  "  function refreshRuns(wait) {",
  '  function refreshRuns(wait) { if (globalThis.deliveryStateStore && typeof globalThis.deliveryStateStore.projection === "function") { return; }',
);
patch(
  "  function servedLive() {",
  '  function servedLive() { if (globalThis.deliveryStateStore && typeof globalThis.deliveryStateStore.projection === "function") { return false; }',
);
patch(
  "        store.subscribe(function (doc) {",
  '        store.subscribe(function (doc) {\n          if (typeof store.projection === "function") { receiveHosted(store.projection()); }',
);
patch(
  "  safely(embedded);\n  var store = globalThis.deliveryStateStore;",
  `  function receiveHosted(payload) {
    if (!payload) { return; }
    VIEW = payload.view; runs = payload.runs; nativeReviews = payload.reviews; readLinks(payload.links);
    PLAN = payload.plan || null; plan = PLAN && isObject(PLAN.plan) ? PLAN.plan : null; NEEDS = payload.needs || null;
    if (PLAN && !parts.plan) {
      var section = el("section", "chapter chapter-plan arrived"); section.id = "plan";
      parts.plan = el("div", "inner"); section.appendChild(parts.plan);
      app.insertBefore(section, parts.happened.parentElement);
      var link = el("a"); link.setAttribute("href", "#plan"); link.setAttribute("aria-label", "The plan");
      add(link, el("span", null, "The plan"), el("i")); rail.insertBefore(link, rail.querySelector('[href="#happened"]'));
    }
    if (parts.plan) { parts.plan.parentElement.hidden = !PLAN; rail.querySelector('[href="#plan"]').hidden = !PLAN; }
  }
  safely(embedded);
  var store = globalThis.deliveryStateStore;`,
);
// Preserve the viewport across a coherent pushed projection. Native code keeps
// subject/fold/focus/line-scroll state; no UI state is rebuilt by the host.
patch(
  "          safely(doc);\n          refreshRuns();",
  '          var hostedY = typeof scrollY === "number" ? scrollY : 0;\n          safely(doc);\n          if (typeof scrollTo === "function") { scrollTo({top: hostedY, behavior: "instant"}); }\n          refreshRuns();',
);
const renderer = (
  await build({
    stdin: {
      contents: (await corpus("run-summary.js")) + "\n" + line,
      resolveDir: new URL("../", import.meta.url).pathname,
    },
    bundle: true,
    format: "iife",
    platform: "browser",
    target: "es2022",
    write: false,
  })
).outputFiles[0].text;
const viewer = (
  await build({
    entryPoints: [new URL("../src/viewer.ts", import.meta.url).pathname],
    bundle: true,
    format: "iife",
    platform: "browser",
    target: "es2022",
    write: false,
  })
).outputFiles[0].text;
const css =
  (await corpus("line-map.css")) +
  "\n" +
  (await corpus("run-summary.css")) +
  "\n" +
  (await readFile(new URL("../src/viewer.css", import.meta.url), "utf8"));
const assets = {};
function asset(text, extension, type) {
  const hash = createHash("sha256").update(text).digest("hex");
  const path = `/assets/${hash}.${extension}`;
  assets[path] = { body: text, type };
  return path;
}
const rendererPath = asset(renderer, "js", "text/javascript; charset=utf-8");
const viewerPath = asset(viewer, "js", "text/javascript; charset=utf-8");
const cssPath = asset(css, "css", "text/css; charset=utf-8");
const source = await readFile(
  new URL("skills/delivery-views/scripts/render.py", root),
  "utf8",
);
const theme = JSON.parse(
  source.match(/^THEME_SWITCH = \((".*")\)$/m)?.[1] ?? "null",
);
if (typeof theme !== "string") throw Error("hosted.theme_hook_drift");
const shell = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow,noarchive"><title>Hosted delivery</title><link rel="stylesheet" href="${cssPath}"><script defer src="${viewerPath}" data-renderer="${rendererPath}"></script></head><body><a href="#app" class="hosted-skip">Skip to delivery</a>${theme}<aside class="hosting-strip" aria-label="Hosting status"><p id="hosting-status" role="status" aria-live="polite">Loading hosted progress…</p><p class="hosting-times" id="hosting-times"></p></aside><main id="app" tabindex="-1" aria-busy="true"><p class="hosted-loading">Loading hosted progress…</p></main><noscript>This read-only delivery view requires JavaScript.</noscript></body></html>`;
await writeFile(
  new URL("../src/generated-assets.ts", import.meta.url),
  `// Generated from trusted corpus assets; do not edit.\nexport const assets: Record<string,{body:string;type:string}> = ${JSON.stringify(assets)};\nexport const shell = ${JSON.stringify(shell)};\n`,
);
await writeFile(
  new URL("../test/assets.generated.json", import.meta.url),
  JSON.stringify(
    {
      renderer,
      viewer,
      css,
      shell,
      paths: { rendererPath, viewerPath, cssPath },
    },
    null,
    2,
  ) + "\n",
);
