// Renders delivery-views pages with the shipped renderer and opens them as a browser would, under jsdom.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const RENDER = path.join(ROOT, "skills", "delivery-views", "scripts", "render.py");
export const CORPUS = path.join(ROOT, "tests", "fixtures", "delivery-views", "line-map");
const PYTHON = process.env.PYTHON || "python3";

export function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

/**
 * A repository holding a configuration, a binding and, when given, a runs command that answers from
 * `runsCommands` (arguments joined by spaces to stdout, null for a failure). Returns its directory.
 */
export function repository({ config, runsCommands = null, renderers = null, stateDirectory = "state" }) {
  const root = mkdtempSync(path.join(tmpdir(), "delivery-views-"));
  spawnSync("git", ["init", "-q"], { cwd: root });
  mkdirSync(path.join(root, ".agents"));
  writeFileSync(path.join(root, ".agents", "delivery-views.json"), JSON.stringify(config));
  writeFileSync(path.join(root, ".agents", "orchestration.json"), JSON.stringify({
    state: {
      directory: stateDirectory,
      renderers: renderers ?? [
        { id: "line-map", argv: ["python3", "-B", "render.py", "line-map"], output: "line-map.html", timeoutSeconds: 30 },
        { id: "history", argv: ["python3", "-B", "render.py", "history"], output: "delivery-history.html", timeoutSeconds: 60 },
      ],
    },
  }));
  if (runsCommands) {
    cpSync(path.join(CORPUS, "replay-runs.py"), path.join(root, "replay-runs.py"));
    writeFileSync(path.join(root, "runs-commands.json"), JSON.stringify(runsCommands));
  }
  return root;
}

/** The runs configuration that replays a repository's recorded runs commands. */
export const REPLAYED_RUNS = { argv: [PYTHON, "-B", "{root}/replay-runs.py", "{root}/runs-commands.json"], timeoutSeconds: 10 };

/** Runs `render.py line-map` in a repository with the document on stdin. */
export function renderIn(root, document, extra = []) {
  const result = spawnSync(PYTHON, ["-B", RENDER, "line-map", "--config", ".agents/delivery-views.json",
    "--binding", ".agents/orchestration.json", ...extra], { cwd: root, input: JSON.stringify(document), encoding: "utf8" });
  return { page: result.stdout, status: result.status, stderr: result.stderr };
}

/** Renders a page in a throwaway repository and returns it, failing on a non-zero exit. */
export function renderPage(document, { config, runsCommands = null } = {}) {
  const root = repository({ config, runsCommands });
  try {
    const { page, status, stderr } = renderIn(root, document);
    if (status !== 0) throw new Error(`render.py exited ${status}: ${stderr}`);
    return page;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

/** One embedded JSON block of a page, parsed. */
export function block(page, id) {
  const found = new RegExp(`<script type="application/json" id="${id}">([^<]*)</script>`).exec(page);
  return found ? JSON.parse(found[1]) : null;
}

const VIEW_ADDRESS = "http://localhost/view/line-map.html";

/**
 * Opens a page and returns what it shows after each step, as the source's harness did. The scenario is
 * { now, store?, server?, rendered?, steps }: `store` puts a fake live store in place ({ read: "pending" |
 * "reject" | "manual" | <document>, subscribeThrows?, onStatusThrows? }); `server: { document }` stands in for
 * the live server's adapter; `rendered: <page>` serves the page at its /view/ address as the server's next
 * render. Steps: { push }, { status }, { resolve }, { event }, { dropped }, { hover }, { leave },
 * { click, index? } (focus, then click), { toggle } or { wait }. Each snapshot is { app, text, focused, source }.
 */
export async function openPage(page, scenario = {}) {
  const listeners = { push: [], status: [] };
  const stream = { document: [], error: [] };
  let answerRead = null;
  const dom = new JSDOM(page, {
    ...(scenario.rendered ? { url: VIEW_ADDRESS } : {}),
    runScripts: "dangerously",
    beforeParse(window) {
      if (scenario.now) window.Date.now = () => Date.parse(scenario.now);
      window.setInterval = () => 0;
      if (scenario.storage === false) {
        Object.defineProperty(window, "localStorage", { get() { throw new Error("storage blocked"); } });
      }
      if (scenario.server) {
        window.EventSource = function (url) {
          if (url !== "/events") throw new Error("unexpected stream " + url);
          this.addEventListener = (kind, listener) => stream[kind].push(listener);
        };
        window.fetch = (url) => url === "/document"
          ? Promise.resolve({ json: () => Promise.resolve(JSON.parse(JSON.stringify(scenario.server.document))) })
          : Promise.reject(new Error("unexpected fetch " + url));
      }
      if (scenario.rendered) {
        window.fetch = (url) => url === VIEW_ADDRESS
          ? Promise.resolve({ text: () => Promise.resolve(scenario.rendered) })
          : Promise.reject(new Error("unexpected fetch " + url));
      }
      const store = scenario.store ?? { read: "pending" };
      if (store) {
        window.deliveryStateStore = {
          read: () => {
            if (store.read === "reject") return Promise.reject(new Error("store down"));
            if (store.read === "manual") return new Promise((resolve) => { answerRead = resolve; });
            if (store.read === "pending" || store.read === undefined) return new Promise(() => {});
            return Promise.resolve(store.read);
          },
          subscribe: (listener) => { if (store.subscribeThrows) throw new Error("no subscribe"); listeners.push.push(listener); },
          onStatus: (listener) => { if (store.onStatusThrows) throw new Error("no status"); listeners.status.push(listener); },
        };
      }
    },
  });
  const { document } = dom.window;
  const app = document.getElementById("app");
  const focused = () => {
    const node = document.activeElement;
    if (!node || node === document.body) return null;
    return node.getAttribute("data-subject") || node.getAttribute("data-ticket") || node.getAttribute("data-tab") || node.tagName.toLowerCase();
  };
  const snapshot = () => {
    const copy = new JSDOM(`<main>${app.innerHTML}</main>`).window.document.querySelector("main");
    return { app: copy, text: copy.textContent ?? "", focused: focused(), source: app.getAttribute("data-source") };
  };
  const snapshots = [snapshot()];
  for (const step of scenario.steps || []) {
    if ("push" in step) listeners.push.forEach((listener) => listener(step.push));
    if ("status" in step) listeners.status.forEach((listener) => listener(step.status));
    if ("resolve" in step) answerRead(step.resolve);
    if ("event" in step) stream.document.forEach((listener) => listener({ data: JSON.stringify(step.event) }));
    if ("dropped" in step) stream.error.forEach((listener) => listener({}));
    if ("hover" in step) document.querySelector(step.hover).dispatchEvent(new dom.window.Event("mouseenter"));
    if ("leave" in step) document.querySelector(step.leave).dispatchEvent(new dom.window.Event("mouseleave"));
    if ("click" in step) { const node = document.querySelectorAll(step.click)[step.index || 0]; node.focus(); node.click(); }
    if ("toggle" in step) { const node = document.querySelector(step.toggle); node.open = !node.open; }
    await new Promise((resolve) => setTimeout(resolve, step.wait || 5));
    snapshots.push(snapshot());
  }
  const storedTheme = (() => { try { return { ...dom.window.localStorage }; } catch { return null; } })();
  dom.window.close();
  return Object.assign(snapshots, { storage: storedTheme });
}

const TOOLS = path.join(ROOT, "tests", "delivery_views_page", "page_tools.py");
function tool(request) {
  const result = spawnSync(PYTHON, ["-B", TOOLS], { input: JSON.stringify(request), encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(`page_tools.py exited ${result.status}: ${result.stderr}`);
  return result.stdout;
}
/** The line map for a document and run summaries given as they are, without a runs command. */
export function pageWithRuns(document, runs = {}, { config = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS }, themeKey, history = null } = {}) {
  return tool({ page: { document, runs, config, ...(themeKey ? { themeKey } : {}), history } });
}
/** The renderer's summary of a run view and its inventory row, with round reports when readings are given. */
export function summarize(view, listed, readings = null, reportSpecs = undefined) {
  return JSON.parse(tool({ summarize: { view, listed, readings, ...(reportSpecs ? { reportSpecs } : {}) } }));
}
/** A value as the page embeds it. */
export function embedded(value) {
  return tool({ embed: value });
}

/** A small expect, so the source's assertions read as they did. */
export function expect(actual) {
  const check = (negate) => ({
    toBe: (expected) => (negate ? assert.notStrictEqual : assert.strictEqual)(actual, expected),
    toEqual: (expected) => (negate ? assert.notDeepStrictEqual : assert.deepStrictEqual)(actual, expected),
    toContain: (expected) => assert.equal(actual.includes(expected), !negate, `${JSON.stringify(actual)?.slice(0, 2000)} ${negate ? "contains" : "lacks"} ${JSON.stringify(expected)}`),
    toBeNull: () => (negate ? assert.notStrictEqual : assert.strictEqual)(actual, null),
    toBeUndefined: () => (negate ? assert.notStrictEqual : assert.strictEqual)(actual, undefined),
    toBeTruthy: () => assert.equal(Boolean(actual), !negate),
    toHaveLength: (expected) => (negate ? assert.notStrictEqual : assert.strictEqual)(actual.length, expected),
    toMatch: (expected) => assert.equal(expected.test(actual), !negate, `${actual} ${negate ? "matches" : "does not match"} ${expected}`),
    toBeGreaterThan: (expected) => assert.equal(actual > expected, !negate, `${actual} > ${expected}`),
    toBeLessThan: (expected) => assert.equal(actual < expected, !negate, `${actual} < ${expected}`),
  });
  return Object.assign(check(false), { not: check(true) });
}
