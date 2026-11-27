// Renders delivery-views pages with the shipped renderer and opens them as a browser would, under jsdom.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { closeSync, cpSync, mkdirSync, mkdtempSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM, VirtualConsole } from "jsdom";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const RENDER = path.join(ROOT, "skills", "delivery-views", "scripts", "render.py");
export const CORPUS = path.join(ROOT, "tests", "fixtures", "delivery-views", "line-map");
const PYTHON = process.env.PYTHON || "python3";

/**
 * spawnSync, with any `input` given to the child as a file it reads for stdin rather than through a pipe: on macOS a
 * pipe that `input` writes into can leave the child waiting on stdin for an end that never comes, and the page lane
 * then runs out its whole limit. A child that still runs past `timeout` is stopped, so the row fails and says so.
 */
export function runSync(command, args, { input, timeout = 120000, ...options } = {}) {
  if (input === undefined) return spawnSync(command, args, { timeout, ...options });
  const folder = mkdtempSync(path.join(tmpdir(), "page-input-"));
  const file = path.join(folder, "stdin");
  writeFileSync(file, input);
  const fd = openSync(file, "r");
  try {
    return spawnSync(command, args, { timeout, ...options, stdio: [fd, "pipe", "pipe"] });
  } finally {
    closeSync(fd);
    rmSync(folder, { recursive: true, force: true });
  }
}

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
  const result = runSync(PYTHON, ["-B", RENDER, "line-map", "--config", ".agents/delivery-views.json",
    "--binding", ".agents/orchestration.json", ...extra], { cwd: root, input: JSON.stringify(document), encoding: "utf8" });
  return { page: result.stdout, status: result.status, stderr: result.stderr };
}

/**
 * Renders a page in a throwaway repository and returns it, failing on a non-zero exit. A `plan` is placed as the
 * state directory's plan.json, `needs` as its needs-you.json and `files` as its lane-changes.json: an object as its JSON,
 * a string as its exact text.
 */
export function renderPage(document, { config, runsCommands = null, plan = null, needs = null, files = null } = {}) {
  const root = repository({ config, runsCommands });
  try {
    for (const [name, value] of [["plan.json", plan], ["needs-you.json", needs], ["lane-changes.json", files]]) {
      if (value === null) continue;
      mkdirSync(path.join(root, "state"), { recursive: true });
      writeFileSync(path.join(root, "state", name), typeof value === "string" ? value : JSON.stringify(value));
    }
    const { page, status, stderr } = renderIn(root, document);
    if (status !== 0) throw new Error(`render.py exited ${status}: ${stderr}`);
    return page;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

export const HISTORY_CORPUS = path.join(ROOT, "tests", "fixtures", "delivery-views", "history");

/**
 * Renders `render.py history` in a throwaway repository: `exports` maps file names to the text they hold in the
 * configured export directory, and `now` is the history's as-of time. Fails on a non-zero exit.
 */
export function renderHistoryPage(document, { config, runsCommands = null, exports = {}, now, renderers = null } = {}) {
  const root = repository({ config, runsCommands, renderers });
  try {
    if (config.exports) {
      const folder = path.join(root, config.exports.directory);
      mkdirSync(folder, { recursive: true });
      for (const [file, text] of Object.entries(exports)) writeFileSync(path.join(folder, file), text);
    }
    const result = runSync(PYTHON, ["-B", RENDER, "history", "--config", ".agents/delivery-views.json",
      "--binding", ".agents/orchestration.json", ...(now ? ["--now", now] : [])],
      { cwd: root, input: JSON.stringify(document), encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(`render.py history exited ${result.status}: ${result.stderr}`);
    return result.stdout;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

export const RUNS_CORPUS = path.join(ROOT, "tests", "fixtures", "delivery-views", "runs");
export const RUNS_RENDERERS = [
  { id: "line-map", argv: ["python3", "-B", "render.py", "line-map"], output: "line-map.html", timeoutSeconds: 30 },
  { id: "history", argv: ["python3", "-B", "render.py", "history"], output: "delivery-history.html", timeoutSeconds: 60 },
  { id: "runs", argv: ["python3", "-B", "render.py", "runs"], output: "runs.html", timeoutSeconds: 60 },
];

/**
 * Renders `render.py runs` (or the `view` given) in a throwaway repository whose runs command replays `runsCommands`,
 * with `now` as the page's as-of time and `watched` as what the live server says of the run store. Fails on a non-zero
 * exit.
 */
export function renderRunsPage(document, { runsCommands, now = "2026-11-20T10:00:00Z", watched = null, renderers = RUNS_RENDERERS,
  view = "runs" } = {}) {
  const root = repository({ config: { spec: "delivery-views-config/1", runs: REPLAYED_RUNS }, runsCommands, renderers });
  try {
    const env = { ...process.env };
    delete env.DELIVERY_LIVE_RUNS;
    if (watched) env.DELIVERY_LIVE_RUNS = watched;
    const result = runSync(PYTHON, ["-B", RENDER, view, "--config", ".agents/delivery-views.json",
      "--binding", ".agents/orchestration.json", ...(view === "line-map" ? [] : ["--now", now])],
      { cwd: root, env, input: JSON.stringify(document), encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(`render.py ${view} exited ${result.status}: ${result.stderr}`);
    return result.stdout;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

/** The page as the live server serves it at /view/: the adapter goes in right after the opt-in marker. */
export function served(page) {
  const result = runSync(PYTHON, ["-B", "-c", [
    "import sys; sys.path.insert(0, sys.argv[1]); import render_state as r",
    "sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8', newline='\\n')",
    "page = sys.stdin.read()",
    "sys.stdout.write(page.replace(r.LIVE_OPT_IN, r.LIVE_OPT_IN + '\\n<script>\\n' + r._LIVE_ADAPTER + '\\n</script>', 1))",
  ].join("\n"), path.join(ROOT, "skills", "orchestrate-work", "scripts")], { input: page, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(`injecting the adapter failed: ${result.stderr}`);
  return result.stdout;
}

/** A snapshot's text, as a golden pins it: every chapter, the story's included, as the page draws it. */
export function pageText(app) {
  return app.textContent ?? "";
}

/** One embedded JSON block of a page, parsed. */
export function block(page, id) {
  const found = new RegExp(`<script type="application/json" id="${id}">([^<]*)</script>`).exec(page);
  return found ? JSON.parse(found[1]) : null;
}

const VIEW_ADDRESS = "http://localhost/view/line-map.html";

/** Where a page's plan chapter ends under `scroll`, from a live page or a snapshot: 10 pixels for each element up to its end. */
export function planEnd(app) {
  const inner = app.querySelector("#plan .inner");
  const everything = [...app.querySelectorAll("*")];
  return everything.filter((node) => node === inner || inner.contains(node) || (node.compareDocumentPosition(inner) & 4) !== 0).length * 10;
}

/**
 * Opens a page and returns what it shows after each step, as the source's harness did. The scenario is
 * { now, store?, server?, rendered?, steps }: `store` puts a fake live store in place ({ read: "pending" |
 * "reject" | "manual" | <document>, subscribeThrows?, onStatusThrows? }); `server: { document }` stands in for
 * the live server's adapter; `rendered: <page>` serves the page at its /view/ address as the server's next
 * render, and a function gives each answer in turn: a page, { refuse: <status>, body }, an Error it fails with, or a promise
 * of one, to hold an answer open. `strayFetch` gives a page with no address a fetch that records any call. Steps: { push }, { status }, { resolve }, { event }, { dropped }, { hover }, { leave },
 * { click, index? } (focus, then click), { toggle }, { run: <function> }, { asked: <n> } (until the page has fetched its address n times;
 * an answer already given is taken in by then, and the snapshot follows the step's wait, so a later ask the page's own timer
 * starts meanwhile, which a loaded machine can delay the snapshot past, may have landed too: hold that answer open with a
 * promise to see the page before it) or { wait }. Each snapshot is { app, text, focused, source,
 * titled }, titled counting the elements anywhere in the document that carry a title attribute.
 *
 * `stored: { key: value }` puts values in the page's storage before it loads; storage needs an address, so it goes with
 * `rendered`.
 *
 * `quicker: <n>` runs the page's timers n times faster and holds its runs refresh, so `asked` counts the plan's asks
 * alone. `scroll: { y }` places the reader, with the plan chapter ending at
 * `planEnd`, and the result's `scrollY` says where the window ended. The result's `fetched` lists fetches of the page's
 * address and `fetchOptions` what each asked for.
 *
 * `layout: { height? }` lays the page out crudely (each shown element a 10-pixel box in document order, holding its shown
 * descendants) in a window `height` tall, 300 by default; every layout read clamps the reader's place to what the page then
 * holds, as a browser's does, and `scrollTo` moves it. Read `scrollY` from a `run` step.
 *
 * `observers: true` gives the page intersection observers that only record what they watch, as `window.__observers`; a
 * `run` step calls one's `callback` to say what came into view.
 *
 * `frames: true` gives the page `requestAnimationFrame` with frames held, not run: a `{ frame: <ms> }` step moves the page's
 * `performance.now()` on by that many milliseconds and runs the frames then waiting, once each, so a count that eases
 * from one number to another can be read where it starts, part way and where it ends.
 *
 * `motion: { reduced? }` stands in for what jsdom lacks to move things: elements get a height that grows with what
 * they hold, `matchMedia` answers the reduced-motion query as `reduced` says, and each `animate` call is recorded, as
 * { target, keyframes, options }, in the result's `animations`. `CSS.highlights` is a registry of sets of ranges, and
 * each snapshot's `held` lists the text of every range its `flip-hold` highlight holds.
 *
 * A `{ run }` step's function is given the page's window.
 */
export async function openPage(page, scenario = {}) {
  const listeners = { push: [], status: [] };
  const stream = { document: [], error: [], runs: [] };
  let answerRead = null;
  const animations = [];
  const intervals = [];
  let frames = [];
  let clock = 0;
  const fetched = [];
  const fetchOptions = [];
  // jsdom can't navigate: a page that reloads itself reports it, and the result's `navigations` counts each.
  const navigations = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.sendTo(console, { omitJSDOMErrors: true });
  virtualConsole.on("jsdomError", (error) => {
    if (/^Not implemented: navigation/.test(error.message)) navigations.push(error.message);
    else console.error(error);
  });
  const dom = new JSDOM(page, {
    virtualConsole,
    // `address` opens the page somewhere other than its own view, as the live server's front page; `fetchAt` is then
    // where the server answers with `rendered`.
    ...(scenario.rendered ? { url: scenario.address ?? VIEW_ADDRESS } : {}),
    runScripts: "dangerously",
    beforeParse(window) {
      if (scenario.now) window.Date.now = () => Date.parse(scenario.now);
      if (scenario.frames) {
        window.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
        Object.defineProperty(window, "performance", { configurable: true, value: { now: () => clock } });
      }
      // Held, not run: a `tick` step runs each one as the page's timer would.
      window.setInterval = (callback) => { intervals.push(callback); return 0; };
      if (scenario.quicker) {
        // The runs refresh fetches the same address, three seconds after a push, so it is held, not run: every fetch a
        // quickened test sees is the plan's, however long a loaded machine makes the test.
        const wait = window.setTimeout.bind(window);
        window.setTimeout = (run, delay, ...rest) => (delay === 3000 ? 0 : wait(run, (delay || 0) / scenario.quicker, ...rest));
      }
      if (scenario.scroll) {
        // What jsdom lacks to place a page: where the reader is, and a plan chapter that ends where what it and the
        // chapters above it hold, at 10 pixels an element, say. A redraw that moves the window goes through `scrollTo`.
        let at = scenario.scroll.y;
        Object.defineProperty(window, "scrollY", { configurable: true, get() { return at; } });
        window.scrollTo = ({ top, behavior }) => { if (behavior !== "instant") throw new Error("the reader's place moved smoothly"); at = top; };
        const sized = window.Element.prototype.getBoundingClientRect;
        window.Element.prototype.getBoundingClientRect = function () {
          if (!this.closest("#plan")) return sized.call(this);
          // The plan's parts span from the page's top to the chapter's end: wholly above the window once it is past them.
          return { bottom: planEnd(window.document.getElementById("app")) - at, top: -at, left: 0, right: 0, width: 0, height: 0 };
        };
      }
      if (scenario.observers) {
        // Intersection observers the page makes, kept on the window as `__observers`, each { callback, observed }, so a
        // step can say what came into view.
        window.__observers = [];
        window.IntersectionObserver = function (callback) {
          const observer = { callback, observed: [] };
          window.__observers.push(observer);
          this.observe = (node) => observer.observed.push(node);
          this.unobserve = () => {};
          this.disconnect = () => {};
        };
      }
      if (scenario.layout) {
        // A page laid out as a browser would, crudely: every element shown is a box 10 pixels tall stacked in document
        // order, holding its shown descendants, and nothing inside `hidden` takes room. The window is `layout.height`
        // tall. As in a browser, every layout read clamps the reader's place to what the page can hold at that moment, so a
        // redraw that measures while a part stands short moves the window unless the page puts the reader back.
        const viewport = scenario.layout.height ?? 300;
        let at = 0;
        let boxes = null;
        const changes = new window.MutationObserver(() => {});
        const laidOut = () => {
          if (boxes && !changes.takeRecords().length) return boxes;
          changes.takeRecords();
          boxes = new Map();
          let row = 0;
          const place = (node) => {
            if (node.hidden || node.tagName === "SCRIPT" || node.tagName === "STYLE") return;
            const top = row;
            row += 1;
            for (const child of node.children) place(child);
            boxes.set(node, { top: top * 10, bottom: row * 10 });
          };
          place(window.document.body);
          boxes.height = row * 10;
          return boxes;
        };
        const flush = () => { const height = laidOut().height; at = Math.max(0, Math.min(at, height - viewport)); return height; };
        Object.defineProperty(window, "innerHeight", { configurable: true, get() { return viewport; } });
        Object.defineProperty(window, "scrollY", { configurable: true, get() { flush(); return at; } });
        Object.defineProperty(window, "pageYOffset", { configurable: true, get() { flush(); return at; } });
        Object.defineProperty(window.HTMLElement.prototype, "scrollHeight", { configurable: true, get() {
          return this === window.document.documentElement || this === window.document.body ? flush() : 0; } });
        window.scrollTo = (x, y) => {
          const top = typeof x === "object" && x !== null ? x.top : y;
          if (typeof top === "number") { at = top; flush(); }
        };
        window.Element.prototype.getBoundingClientRect = function () {
          flush();
          const box = boxes.get(this);
          if (!box) return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
          return { top: box.top - at, bottom: box.bottom - at, left: 0, right: 100, width: 100, height: box.bottom - box.top, x: 0, y: box.top - at };
        };
        if (!scenario.motion) {
          Object.defineProperty(window.HTMLElement.prototype, "offsetHeight", { configurable: true, get() {
            flush();
            const box = boxes.get(this);
            return box ? box.bottom - box.top : 0;
          } });
        }
        changes.observe(window.document, { subtree: true, childList: true, attributes: true });
      }
      if (scenario.motion) {
        const reduced = Boolean(scenario.motion.reduced);
        window.matchMedia = (query) => ({ media: query, matches: query.includes("prefers-reduced-motion: reduce") && reduced,
          addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
        window.Highlight = class Highlight extends Set {};
        window.CSS = { highlights: new Map() };
        // A height that grows with what a reader would see: a closed <details> shows only its summary.
        const shown = (node) => [...node.children].reduce((sum, child) => sum + 1 + (child.tagName === "DETAILS" && !child.open
          ? [...child.children].filter((part) => part.tagName === "SUMMARY").reduce((inner, part) => inner + 1 + shown(part), 0)
          : shown(child)), 0);
        Object.defineProperty(window.HTMLElement.prototype, "offsetHeight", {
          configurable: true, get() { return 10 + 10 * (this.tagName === "DETAILS" && !this.open
            ? [...this.children].filter((part) => part.tagName === "SUMMARY").reduce((sum, part) => sum + 1 + shown(part), 0)
            : shown(this)); } });
        // Each animation finishes at once, as a browser's would once its duration passed, so what a page does
        // when one ends takes place before the next snapshot.
        window.Element.prototype.animate = function (keyframes, options) {
          animations.push({ target: this.id || this.className, keyframes, options });
          return { cancel() {}, finish() {}, oncancel: null, get onfinish() { return null; },
            set onfinish(done) { if (typeof done === "function") queueMicrotask(done); } };
        };
      }
      for (const [key, value] of Object.entries(scenario.stored ?? {})) window.localStorage.setItem(key, value);
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
        const fromServer = window.fetch;
        window.fetch = (url, options) => {
          // With the server's adapter too, its document is still read where it lives.
          if (url === "/document" && scenario.server) return fromServer(url, options);
          if (url !== (scenario.fetchAt ?? VIEW_ADDRESS)) return Promise.reject(new Error("unexpected fetch " + url));
          fetched.push(url);
          fetchOptions.push(options);
          const answer = typeof scenario.rendered === "function" ? scenario.rendered() : scenario.rendered;
          return Promise.resolve(answer).then((given) => {
            if (given instanceof Error) throw given;
            if (typeof given === "object") return { ok: false, status: given.refuse, text: () => Promise.resolve(given.body) };
            return { ok: true, status: 200, text: () => Promise.resolve(given) };
          });
        };
      }
      if (scenario.strayFetch) {
        window.fetch = (url, options) => { fetched.push(url); fetchOptions.push(options); return Promise.reject(new Error("no ask expected")); };
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
    const hold = dom.window.CSS?.highlights?.get("flip-hold");
    return { app: copy, text: copy.textContent ?? "", focused: focused(), source: app.getAttribute("data-source"),
      titled: document.querySelectorAll("[title]").length,
      held: hold ? [...hold].map((range) => range.startContainer.data.slice(range.startOffset, range.endOffset)) : [] };
  };
  const snapshots = [snapshot()];
  for (const step of scenario.steps || []) {
    if ("push" in step) listeners.push.forEach((listener) => listener(step.push));
    if ("status" in step) listeners.status.forEach((listener) => listener(step.status));
    if ("resolve" in step) answerRead(step.resolve);
    if ("event" in step) stream.document.forEach((listener) => listener({ data: JSON.stringify(step.event) }));
    // The live server's word that the runs moved, its data exactly as the server writes it.
    if ("runs" in step) stream.runs.forEach((listener) => listener({ data: step.runs }));
    if ("dropped" in step) stream.error.forEach((listener) => listener({}));
    if ("hover" in step) document.querySelector(step.hover).dispatchEvent(new dom.window.Event("mouseenter"));
    if ("leave" in step) document.querySelector(step.leave).dispatchEvent(new dom.window.Event("mouseleave"));
    if ("click" in step) {
      const node = document.querySelectorAll(step.click)[step.index || 0];
      node.focus();
      // A click on a link to another part of the page navigates: jsdom follows it, and the page hears of it, on tasks
      // after the click returns. The snapshot waits for the page to turn, as a reader sees it only once it has.
      const link = node.closest ? node.closest("a[href]") : null;
      const to = link ? new URL(link.href) : null;
      const here = new URL(dom.window.location.href);
      const navigates = Boolean(to && to.hash && to.hash !== here.hash && to.origin === here.origin
        && to.pathname === here.pathname && to.search === here.search);
      let prevented = false;
      dom.window.addEventListener("click", (event) => { prevented = event.defaultPrevented; }, { once: true });
      const turned = new Promise((resolve) => dom.window.addEventListener("hashchange", resolve, { once: true }));
      // An SVG element, such as a tick on the line, has no click(): it is clicked as a pointer would.
      if (typeof node.click === "function") node.click();
      else node.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      if (navigates && !prevented) {
        let late;
        const bound = new Promise((_, reject) => { late = setTimeout(() => reject(new Error(`no hashchange for ${step.click}`)), 10000); });
        await Promise.race([turned, bound]).finally(() => clearTimeout(late));
      }
    }
    if ("toggle" in step) { const node = document.querySelector(step.toggle); node.open = !node.open; }
    if ("tick" in step) intervals.forEach((callback) => callback());
    if ("frame" in step) {
      clock += step.frame;
      const waiting = frames;
      frames = [];
      waiting.forEach((callback) => callback(clock));
    }
    if ("run" in step) step.run(dom.window);
    if ("asked" in step) {
      const deadline = Date.now() + 20000;
      while (fetched.length < step.asked) {
        if (Date.now() > deadline) throw new Error(`the page fetched ${fetched.length} times, not ${step.asked}`);
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
    }
    await new Promise((resolve) => setTimeout(resolve, step.wait || 5));
    snapshots.push(snapshot());
  }
  const scrollY = dom.window.scrollY;
  const storedTheme = (() => { try { return { ...dom.window.localStorage }; } catch { return null; } })();
  dom.window.close();
  return Object.assign(snapshots, { storage: storedTheme, animations, fetched, fetchOptions, scrollY, navigations });
}

const TOOLS = path.join(ROOT, "tests", "delivery_views_page", "page_tools.py");
function tool(request) {
  const result = runSync(PYTHON, ["-B", TOOLS], { input: JSON.stringify(request), encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(`page_tools.py exited ${result.status}: ${result.stderr}`);
  return result.stdout;
}
/**
 * The line map for a document and run summaries given as they are, without a runs command. A `plan` is validated and
 * embedded as the renderer embeds plan.json; a `planBlock` is embedded as it is given.
 */
export function pageWithRuns(document, runs = {}, { config = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS }, themeKey, history = null,
  plan, planBlock } = {}) {
  return tool({ page: { document, runs, config, ...(themeKey ? { themeKey } : {}), history, ...(plan !== undefined ? { plan } : {}),
    ...(planBlock !== undefined ? { planBlock } : {}) } });
}
/** The renderer's summary of a run view and its inventory row, with round reports when readings are given. */
export function summarize(view, listed, readings = null, reportSpecs = undefined) {
  return JSON.parse(tool({ summarize: { view, listed, readings, ...(reportSpecs ? { reportSpecs } : {}) } }));
}
/** orchestrate-work's built-in page for a record, its story told with the registry at `registry` when given. */
export function builtinPage(document, registry = null) {
  return tool({ builtin: { document, registry } });
}
/** A record as the state wrapper gives it to every view: with what its sibling deliveries show. */
export function withAcross(document) {
  return JSON.parse(tool({ across: document }));
}
/** A record as the live server sends it: with what everything in it waits on, derived afresh by the state tool. */
export function waited(document) {
  return JSON.parse(tool({ waited: document }));
}
/** A record as the live server sends it: with the story the narrator tells of it. */
export function told(document) {
  return JSON.parse(tool({ told: document }));
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
