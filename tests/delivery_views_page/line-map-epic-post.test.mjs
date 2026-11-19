// An epic split by codebase has a delivery per repository. Past this repository's finish line the main line runs on to
// a post for the whole epic, where up to two sibling repositories turn in from rows under this repository's lanes, the
// rest folded into a chip. The line reads only what the state wrapper words of the siblings, `across.groups`: a group of
// another epic draws nothing on it. With no same-epic sibling the line is drawn exactly as it always was.
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { ROOT, expect, openPage, pageWithRuns, withAcross } from "./support.mjs";
import { item, journal, record } from "./plan-fixtures.mjs";

process.env.TZ = "UTC";

const NOW = "2026-10-30T12:00:00Z";
// The last read that changed a sibling, and when its latest outcome began.
const READ = "2026-10-30T11:50:00Z";
const ATTEMPT = "2026-10-30T11:55:00Z";
const CONFIG = { spec: "delivery-views-config/1" };
const LINK = "https://views.example/d/" + "a".repeat(64);
const CSS = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "line-map.css"), "utf8");

/** A sibling as reconcile leaves it, its stories keyed by its name. `never` is one never read and never tried. */
function sibling(name, stages, outcome = "read", fields = {}) {
  const stories = stages.map((stage, index) => ({ subject: `${name.toUpperCase()}-${index + 1}`, title: `${name} ${index + 1}`, stage }));
  const read = outcome === "never" ? {} : { readAt: READ, stories };
  return { name, link: LINK, addedAt: "2026-10-30T10:00:00Z",
    ...(outcome === "never" ? {} : { lastOutcome: outcome, lastAttemptAt: ATTEMPT }), ...read, ...fields };
}
/** One tried but never read: an outcome since its attempt, and no read at all. */
function tried(name) {
  return { name, link: LINK, addedAt: "2026-10-30T10:00:00Z", lastOutcome: "unavailable", lastAttemptAt: ATTEMPT };
}
/**
 * This delivery's record: a lane per entry of `lanes`, each a list of its pieces' stages, keyed APP-21 on; its siblings
 * as the state wrapper words them; and the waits given.
 */
function document({ lanes = [["merged"], ["merged"]], siblings, waits, checking } = {}) {
  let next = 21;
  const queue = lanes.flatMap((stages, lane) => stages.map((stage) => item(`APP-${next++}`, stage, { lane: `lane-${lane + 1}` })));
  const shown = record({ queue, journal: journal([[10, "lane.assigned", "APP-21"]]), meta: { trackerParent: "APP-10" } });
  if (checking) shown.state.meta.checking = { now: checking, next: [] };
  if (siblings !== undefined) shown.state.siblings = siblings;
  const doc = withAcross(shown);
  if (waits) doc.waits = waits;
  return doc;
}
/** A wait of this repository's piece on a sibling's story. */
const waitOn = (subject, on) => ({ subject, on, cause: "sibling", words: `Waits on ${on}`, stage: "planned", frees: "landed", needsSomeone: false });

/**
 * The page opened, then drawn in light and then in dark: the drawing is the same in both, since every colour is a
 * theme token. Returns the light snapshot and the dark one, after checking each theme took.
 */
async function both(doc, steps = []) {
  const themes = [];
  const seen = { run: (window) => themes.push(window.document.documentElement.getAttribute("data-theme")) };
  const snaps = await openPage(pageWithRuns(doc, {}, { config: CONFIG }), { now: NOW, store: { read: "pending" },
    steps: [{ click: '[data-theme-pick="light"]' }, seen, { click: '[data-theme-pick="dark"]' }, seen, ...steps] });
  expect(themes).toEqual(["light", "dark"]);
  const light = snaps[2], dark = snaps[4];
  expect(dark.app.querySelector("#line").innerHTML).toBe(light.app.querySelector("#line").innerHTML);
  return [light, dark, ...snaps.slice(5)];
}

const svgOf = (snap) => snap.app.querySelector(".line-merge");
const trackOf = (snap) => snap.app.querySelector(".lines-track");
/** A custom property the track's own style sets. */
function property(snap, name) {
  const found = (trackOf(snap).getAttribute("style") || "").split(";").map((part) => part.trim().split(/:\s*/))
    .find(([key]) => key === name);
  return found ? found[1] : null;
}
const classOf = (svg, selector) => [...svg.querySelectorAll(selector)].map((node) => node.getAttribute("class"));
const pathOf = (svg, selector) => [...svg.querySelectorAll(selector)].map((node) => [node.getAttribute("class"), node.getAttribute("d")]);
/** The drawing's texts, without a flip's layers: class, x, y and words. */
function texts(svg) {
  return [...svg.querySelectorAll("text")].filter((node) => !/flip-(in|out)/.test(node.getAttribute("class")))
    .map((node) => [node.getAttribute("class"), Number(node.getAttribute("x")), Number(node.getAttribute("y")), node.textContent]);
}
const rows = (snap) => [...snap.app.querySelectorAll(".line.sibling")].map((row) => row.querySelector(".name").textContent);
const aria = (snap) => svgOf(snap).getAttribute("aria-label");

// Today's drawing, captured before the epic's post was added, for one lane landed and for two lanes, one still open.
const TODAY_ONE = '<svg class="line-merge" viewBox="0 0 176 46" width="176" height="46" role="img" aria-label="Every lane merges into one main line: 1 of 1 pieces landed"><path class="mg-trunk" d="M100 23H162"></path><path class="mg-turn" d="M0 23H12C64 23 48 23 100 23" style="--c: var(--l1);"></path><circle class="mg-join" cx="100" cy="23" r="4"></circle><path class="mg-post" d="M162 3V43"></path><text class="mg-label" x="165" y="-7" text-anchor="end">Finish line</text><text class="mg-count" x="165" y="61" text-anchor="end">1/1 landed</text></svg>';
const TODAY_TWO = '<svg class="line-merge" viewBox="0 0 176 130" width="176" height="130" role="img" aria-label="Every lane merges into one main line: 1 of 2 pieces landed"><path class="mg-trunk open" d="M100 65H162"></path><path class="mg-turn" d="M0 23H12C64 23 48 65 100 65" style="--c: var(--l1);"></path><path class="mg-turn open" d="M0 107H12C64 107 48 65 100 65" style="--c: var(--l2);"></path><circle class="mg-join" cx="100" cy="65" r="4"></circle><path class="mg-post" d="M162 45V85"></path><text class="mg-label" x="165" y="35" text-anchor="end">Finish line</text><text class="mg-count" x="165" y="103" text-anchor="end">1/2 landed</text></svg>';

test("with no same-epic sibling the line is today's, and so it is with only another epic's group", async () => {
  const cases = [[{ lanes: [["merged"]] }, TODAY_ONE], [{ lanes: [["merged"], ["building"]] }, TODAY_TWO],
    [{ lanes: [["merged"]], siblings: [sibling("legacy", ["building"], "other-epic")] }, TODAY_ONE],
    [{ lanes: [["merged"], ["building"]], siblings: [sibling("legacy", ["merged"], "other-epic")] }, TODAY_TWO]];
  for (const [given, today] of cases) {
    for (const snap of await both(document(given))) {
      expect(svgOf(snap).outerHTML).toBe(today);
      expect([property(snap, "--merge-width"), property(snap, "--merge-top")]).toEqual(["176px", "16px"]);
      expect(snap.app.querySelectorAll(".line.sibling, .more-chip, .mg-epic, .mg-sib")).toHaveLength(0);
    }
  }
});

test("waiting: both posts are drawn, the epic's words above its own, and a sibling with open stories turns in dashed", async () => {
  for (const snap of await both(document({ siblings: [sibling("api", ["building", "merged"])] }))) {
    const svg = svgOf(snap);
    expect([svg.getAttribute("width"), svg.getAttribute("viewBox"), svg.getAttribute("height")]).toEqual(["304", "0 0 304 214", "214"]);
    expect([property(snap, "--merge-width"), property(snap, "--merge-top")]).toEqual(["304px", "16px"]);
    // This repository's trunk and post keep their place; the epic's line runs on past it to the epic's post.
    expect(pathOf(svg, ".mg-trunk, .mg-post, .mg-epic, .mg-epic-post, .mg-sib")).toEqual([["mg-trunk", "M100 65H162"],
      ["mg-epic open", "M162 65H290"], ["mg-sib open", "M0 191H12C150 191 190 65 228 65"], ["mg-post", "M162 45V85"],
      ["mg-epic-post open", "M290 45V85"]]);
    expect(texts(svg)).toEqual([["mg-label", 165, 35, "Finish line"], ["mg-count", 165, 103, "2/2 landed"],
      ["mg-label", 293, 21, "Epic"], ["mg-count", 293, 35, "3/4 landed"]]);
    // The sibling's row sits under this repository's lanes, with its count and no stops.
    expect(rows(snap)).toEqual(["api1 of 2 landed"]);
    expect(snap.app.querySelector(".line.sibling .stop-btn")).toBeNull();
    expect([...trackOf(snap).children].map((node) => node.getAttribute("class"))).toEqual(["line", "line", "line sibling open", "line-merge"]);
    expect(aria(snap)).toBe("Every lane merges into one main line: 2 of 2 pieces landed; the epic: 3 of 4 pieces landed across 2 repositories");
  }
});

test("mixed epics: only the same-epic sibling is drawn and counted, and the other is named under Also linked", async () => {
  const doc = document({ lanes: [["merged"], ["building"]], siblings: [sibling("legacy", ["merged", "merged"], "other-epic"), sibling("api", ["review"])] });
  for (const snap of await both(doc)) {
    const svg = svgOf(snap);
    expect(rows(snap)).toEqual(["api0 of 1 landed"]);
    expect(classOf(svg, ".mg-sib")).toEqual(["mg-sib open"]);
    expect(classOf(svg, ".mg-epic, .mg-epic-post, .mg-label")).toEqual(["mg-epic open", "mg-label", "mg-epic-post open", "mg-label"]);
    expect(texts(svg).slice(2)).toEqual([["mg-label", 293, 21, "Epic"], ["mg-count", 293, 35, "1/3 landed"]]);
    expect(aria(snap)).toBe("Every lane merges into one main line: 1 of 2 pieces landed; the epic: 1 of 3 pieces landed across 2 repositories");
    const chapter = snap.app.querySelector("#across");
    expect(chapter.querySelector(".title").textContent).toBe("1 other repository delivers this epic.");
    expect([...chapter.querySelectorAll(".across-group .across-name")].map((node) => node.textContent)).toEqual(["api"]);
    const also = chapter.querySelector(".across-also");
    expect(also.textContent).toBe("Also linkedlegacyDelivers another epic since 5 minutes ago");
    // After the drawn groups.
    expect(also.previousElementSibling.classList.contains("across-fold")).toBe(true);
  }
});

test("met: every sibling turns in solid, and so do the epic's line and post", async () => {
  const doc = document({ siblings: [sibling("api", ["merged"]), sibling("docs", ["settled", "merged"])] });
  for (const snap of await both(doc)) {
    const svg = svgOf(snap);
    expect(classOf(svg, ".mg-sib, .mg-epic, .mg-epic-post")).toEqual(["mg-epic", "mg-sib", "mg-sib", "mg-epic-post"]);
    expect(texts(svg).slice(2)).toEqual([["mg-label", 293, 21, "Epic"], ["mg-count", 293, 35, "5/5 landed"]]);
    expect(rows(snap)).toEqual(["api1 of 1 landed", "docs2 of 2 landed"]);
    expect(svg.getAttribute("height")).toBe(String(3 * 84 + 46));
  }
});

test("nearly met: each sibling turns in solid, but one open piece here keeps the epic's line dashed and its count one short", async () => {
  const doc = document({ lanes: [["merged"], ["building"]], siblings: [sibling("api", ["merged"]), sibling("docs", ["merged"])] });
  for (const snap of await both(doc)) {
    const svg = svgOf(snap);
    expect(classOf(svg, ".mg-sib, .mg-epic, .mg-epic-post")).toEqual(["mg-epic open", "mg-sib", "mg-sib", "mg-epic-post open"]);
    expect(texts(svg).slice(2)).toEqual([["mg-label", 293, 21, "Epic"], ["mg-count", 293, 35, "3/4 landed"]]);
  }
});

test("one unreadable sibling turns in dotted, and the epic's line and post go dashed and neutral, naming it", async () => {
  const doc = document({ siblings: [sibling("api", ["building"], "stale")] });
  for (const snap of await both(doc)) {
    const svg = svgOf(snap);
    expect(classOf(svg, ".mg-sib, .mg-epic, .mg-epic-post")).toEqual(["mg-epic unread", "mg-sib unread", "mg-epic-post unread"]);
    expect(texts(svg).slice(2)).toEqual([["mg-label unread", 293, 7, "Epic ?"], ["mg-count", 293, 21, "api"], ["mg-count", 293, 35, "2/3 landed"]]);
    expect(rows(snap)).toEqual(["apiCouldn't be read since 5 minutes ago"]);
    expect(snap.app.querySelector(".line.sibling time").getAttribute("datetime")).toBe(ATTEMPT);
    expect(aria(snap)).toBe("Every lane merges into one main line: 2 of 2 pieces landed; the epic: 2 of 3 pieces landed across 2 repositories, api can't be read");
  }
});

test("two unreadable: the count of them goes under the label, and one never read ranks as furthest behind", async () => {
  const doc = document({ siblings: [sibling("api", ["merged", "building", "building"], "stale"), tried("web")] });
  for (const snap of await both(doc)) {
    const svg = svgOf(snap);
    expect(texts(svg).slice(2)).toEqual([["mg-label unread", 293, 7, "Epic ?"], ["mg-count", 293, 21, "2 can't be read"],
      ["mg-count", 293, 35, "3/5 landed"]]);
    // The never-read one has no stories and so a landed share of 0: it is drawn first, and reads Never read whatever its since.
    expect(rows(snap)).toEqual(["webNever read", "apiCouldn't be read since 5 minutes ago"]);
    expect(classOf(svg, ".mg-sib")).toEqual(["mg-sib unread", "mg-sib unread"]);
    expect(aria(snap)).toBe("Every lane merges into one main line: 2 of 2 pieces landed; the epic: 3 of 5 pieces landed across 3 repositories, api and web can't be read");
  }
});

test("many: the waited-on sibling and the furthest behind are drawn, the rest fold into a chip, and a redraw re-ranks", async () => {
  const many = (gamma) => document({ lanes: [["merged"], ["building"]], waits: [waitOn("APP-22", "ALPHA-3")], siblings: [
    sibling("delta", []), sibling("alpha", ["merged", "merged", "building"]), sibling("eps", ["merged", "building"]),
    sibling("gamma", gamma), sibling("beta", ["building", "building"])] });
  const [light, dark, pushed] = await both(many(["building"]), [{ push: many(["building", "building", "building"]) }]);
  for (const snap of [light, dark]) {
    // alpha has the highest share landed: only the wait puts it first. beta and gamma share the lowest; beta has more open.
    expect(rows(snap)).toEqual(["alpha2 of 3 landed", "beta0 of 2 landed"]);
    const chip = snap.app.querySelector(".more-chip");
    expect(chip.textContent).toBe("+3 more");
    expect(chip.getAttribute("aria-label")).toBe("+3 more: gamma, eps and delta");
    expect(texts(svgOf(snap)).slice(2)).toEqual([["mg-label", 293, 21, "Epic"], ["mg-count", 293, 35, "4/10 landed"]]);
    expect(aria(snap)).toBe("Every lane merges into one main line: 1 of 2 pieces landed; the epic: 4 of 10 pieces landed across 6 repositories, 3 more not drawn");
  }
  // gamma now has the most open of the lowest: it is drawn second, and the epic's count flips.
  expect(rows(pushed)).toEqual(["alpha2 of 3 landed", "gamma0 of 3 landed"]);
  expect(pushed.app.querySelector(".more-chip").getAttribute("aria-label")).toBe("+3 more: beta, eps and delta");
  expect(texts(svgOf(pushed)).slice(2)).toEqual([["mg-label", 293, 21, "Epic"], ["mg-count", 293, 35, "4/12 landed"]]);
  expect([...svgOf(pushed).querySelectorAll("text.flip-out, text.flip-in")].map((node) => [node.getAttribute("class"), node.textContent]))
    .toEqual([["mg-count flip-out", "4/10 landed"], ["mg-count flip-in", "4/12 landed"]]);
});

// The new rules, by selector, as the stylesheet holds them.
const STROKED = [".mg-sib", ".mg-sib.open", ".mg-sib.unread", ".mg-epic", ".mg-epic.open", ".mg-epic.unread",
  ".mg-epic-post", ".mg-epic-post.open", ".mg-epic-post.unread"];
const NEW_RULES = [...STROKED, ".mg-epic-join", ".mg-label.unread", ".line.sibling .name b", ".line.sibling .name span", ".more-chip"];
/** A rule's body, by its exact selector at the start of a line. */
function rule(selector, from = CSS) {
  const at = from.search(new RegExp("^\\s*" + selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " \\{", "m"));
  expect([selector, at >= 0]).toEqual([selector, true]);
  const open = from.indexOf("{", at);
  return from.slice(open + 1, from.indexOf("}", open)).trim();
}
const declaration = (body, name) => (body.split(";").map((part) => part.trim()).find((part) => part.startsWith(name + ":")) || "").slice(name.length + 1).trim();

test("the stylesheet: every new stroke is --edge, dashed and dotted differ, and the merge's width and headroom are one variable each", async () => {
  for (const selector of STROKED) expect([selector, declaration(rule(selector), "stroke")]).toEqual([selector, "var(--edge)"]);
  const dashes = Object.fromEntries(STROKED.map((selector) => [selector, declaration(rule(selector), "stroke-dasharray")]));
  for (const solid of [".mg-sib", ".mg-epic", ".mg-epic-post"]) expect([solid, dashes[solid]]).toEqual([solid, ""]);
  for (const broken of STROKED.filter((selector) => /\.(open|unread)$/.test(selector))) expect([broken, dashes[broken] !== ""]).toEqual([broken, true]);
  expect(dashes[".mg-sib.open"]).not.toBe(dashes[".mg-sib.unread"]);
  // The width and the room above the line are written from the script's variables, never as numbers of their own.
  const track = rule(".lines-track");
  expect(declaration(track, "padding-right")).toBe("var(--merge-width)");
  expect(declaration(track, "padding-top")).toBe("var(--merge-top)");
  expect(declaration(track, "min-width")).toBe("calc(244px + var(--merge-width) + var(--most, 1) * 46px)");
  expect(declaration(rule(".line-merge"), "top")).toBe("var(--merge-top)");
  const phone = CSS.slice(CSS.indexOf("@media (max-width: 640px)"));
  expect(declaration(rule(".lines-track", phone), "min-width")).toBe("calc(116px + var(--merge-width) + var(--most, 1) * 46px)");
  expect(declaration(rule(".line", phone), "grid-template-columns")).toBe("104px minmax(0, 1fr)");
  for (const body of [track, rule(".lines-track", phone)]) for (const literal of ["176px", "420px", "292px"]) expect(body).not.toContain(literal);
  for (const body of [declaration(track, "padding-top"), declaration(rule(".line-merge"), "top")]) expect(body).not.toContain("16px");
  const lines = rule(".lines");
  expect(declaration(lines, "margin-block")).toBe("-16px");
  expect(lines).not.toContain("--merge-top");
  // Every token the new rules colour with is a theme token both themes define, and none is a warning colour.
  const used = [...new Set(NEW_RULES.flatMap((selector) => [...rule(selector).matchAll(/var\(--([a-z0-9-]+)/g)].map((match) => match[1])))]
    .filter((name) => !["merge-top", "merge-width", "chip-y", "most", "mono", "text"].includes(name));
  expect(used.length).toBeGreaterThan(0);
  expect(used.filter((name) => ["held", "wait"].includes(name))).toEqual([]);
  for (const scope of [":root", ':root:not([data-theme="light"])', ':root[data-theme="dark"]']) {
    const defined = rule(scope);
    for (const token of used) expect([scope, token, defined.includes(`--${token}:`)]).toEqual([scope, token, true]);
  }
  // Each new rule matches something drawn in one of the states.
  const states = [document({ siblings: [sibling("api", ["building"])] }), document({ siblings: [sibling("api", ["merged"])] }),
    document({ lanes: [["building"]], siblings: [sibling("api", ["building"], "stale"), sibling("b", []), sibling("c", []), sibling("d", [])] })];
  const drawn = [];
  for (const doc of states) drawn.push(...(await both(doc)));
  for (const selector of NEW_RULES) expect([selector, drawn.some((snap) => snap.app.querySelector(selector) !== null)]).toEqual([selector, true]);
});

test("headroom: with one lane and an unreadable sibling, every word of the epic's post lies inside the track", async () => {
  const doc = document({ lanes: [["merged"]], siblings: [sibling("api", ["building"], "stale")] });
  for (const snap of await both(doc)) {
    const top = Number.parseFloat(property(snap, "--merge-top"));
    expect(top).toBeGreaterThan(16);
    const size = { "mg-label": 13, "mg-label unread": 13, "mg-count": 11 };
    const epic = texts(svgOf(snap)).filter(([, x]) => x === 293);
    expect(epic.map(([, , , words]) => words)).toEqual(["Epic ?", "api", "1/2 landed"]);
    for (const [cls, , y, words] of epic) expect([words, y + top - size[cls] >= 0]).toEqual([words, true]);
  }
});

test("motion: the stream down the main line stops at this repository's post, at the drawn trunk's height", async () => {
  const doc = () => document({ lanes: [["merged"], ["checking"]], siblings: [sibling("api", ["building"])],
    checking: { subject: "APP-22", steps: [{ id: "suite", state: "running" }] } });
  const WIDTH = 1000;
  const lay = (window) => {
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { configurable: true,
      get() { return this.classList.contains("lines-track") ? WIDTH : 0; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { configurable: true,
      get() { return this.classList.contains("lines-track") ? 300 : 0; } });
    window.Element.prototype.getBoundingClientRect = function () {
      const left = this.classList.contains("stop-btn") ? 500 : 0;
      return { left, right: left + 18, top: 0, bottom: 18, width: this.classList.contains("stop-btn") ? 18 : WIDTH, height: 18 };
    };
    const computed = window.getComputedStyle.bind(window);
    window.getComputedStyle = (node) => node.classList && node.classList.contains("lines-track")
      ? { paddingTop: node.style.getPropertyValue("--merge-top") } : computed(node);
  };
  const snaps = await openPage(pageWithRuns(doc(), {}, { config: CONFIG }), { now: NOW, store: { read: "pending" }, motion: {},
    steps: [{ run: lay }, { push: doc() }] });
  const drawn = snaps[2];
  expect(property(drawn, "--merge-top")).toBe("16px");
  const trunk = drawn.app.querySelector(".line-flow path.flow-trunk");
  expect(trunk).not.toBeNull();
  // The trunk is drawn 65 down the drawing, which sits 16 down the track.
  expect(trunk.getAttribute("d")).toBe(`M${WIDTH - 304 + 100} 81H${WIDTH - 304 + 162}`);
});
