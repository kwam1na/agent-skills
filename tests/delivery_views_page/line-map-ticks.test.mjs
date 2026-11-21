// Where the lanes rejoin the main line, every story is a tick on it: landed first, then the open ones nearest to landing
// first, in groups of five, a caret on the one the queue lands next. A lane row stays one width at any story count. A
// delivery rejoining its target draws one bar of its four steps and bends into the target at the finish post; one going
// straight to its base ends at the post. Every tick, step and finish square is reached from the keyboard and names itself.
import { test } from "node:test";

import { REPLAYED_RUNS, expect, openPage, pageWithRuns } from "./support.mjs";
import { headOf, item, journal, record } from "./plan-fixtures.mjs";

process.env.TZ = "UTC";

const NOW = "2026-10-30T12:00:00Z";
const CONFIG = { spec: "delivery-views-config/1", runs: REPLAYED_RUNS };
const KEY = "APP-10";
// The tick states in the order the line draws them, nearest to landing first.
const ORDER = ["landed", "queued", "review", "building", "stopped", "planned"];
// How each state is held in the record: landed, queued and stopped are queue elements; the rest are planned work.
const HELD = { landed: ["queue", "merged"], queued: ["queue", "queued"], pending: ["queue", "queued", { pending: true }],
  checking: ["queue", "checking"], stopped: ["queue", "stopped"],
  review: ["planned", "review"], building: ["planned", "building"], planned: ["planned", "planned"] };

/** A delivery with one lane per entry of `lanes`, each listing its stories' states in its own order. */
function delivery(lanes, { integration = null, steps = [], finish = [] } = {}) {
  const queue = [], planned = [];
  let next = 1;
  lanes.forEach((states, lane) => states.forEach((state) => {
    const [where, stage, extra = {}] = HELD[state];
    const subject = `APP-${next++}`;
    (where === "queue" ? queue : planned).push(where === "queue" ? item(subject, stage, { lane: `lane-${lane + 1}`, ...extra })
      : { subject, title: `Work ${subject}`, lane: `lane-${lane + 1}`, stage });
  }));
  const entries = journal(steps);
  entries.forEach((entry) => { if (entry.subject === KEY) entry.lane = null; });
  const doc = record({ queue, planned, journal: entries, finish,
    meta: { trackerParent: KEY, baseHead: headOf("base"), ...(integration ? { integration } : {}) } });
  doc.state.lanes = lanes.map((_, lane) => ({ id: `lane-${lane + 1}`, title: `Lane ${lane + 1}`, focus: "", state: "working", waitingOn: null,
    since: "2026-10-30T10:00:00Z" }));
  return doc;
}
const open = (doc, steps = []) => openPage(pageWithRuns(doc, {}, { config: CONFIG }), { now: NOW, store: false, steps });
const ticks = (app) => [...app.querySelectorAll("#line .mg-tick")];
const tickX = (tick) => Number(tick.querySelector(".mg-tick-mark").getAttribute("x")) + 2.5;
// A delivery of `total` stories over `lanes` lanes, each lane cycling through every state from a different start.
function sized(total, laneCount) {
  const lanes = Array.from({ length: laneCount }, () => []);
  for (let i = 0; i < total; i += 1) lanes[i % laneCount].push(ORDER[(i * 7 + Math.floor(i / laneCount)) % ORDER.length]);
  return lanes;
}

for (const [total, laneCount] of [[5, 2], [24, 6], [48, 8]]) {
  test(`${total} stories over ${laneCount} lanes: one tick each, landed first then nearest to landing, in fives, the next one marked`, async () => {
    const lanes = sized(total, laneCount);
    const [{ app }] = await open(delivery(lanes));
    const drawn = ticks(app);
    expect(drawn).toHaveLength(total);
    // By state, nearest to landing first, and within a state in the lanes' order.
    const states = drawn.map((tick) => tick.getAttribute("data-state"));
    const ranked = drawn.map((tick) => [ORDER.indexOf(tick.getAttribute("data-state")), Number(tick.getAttribute("data-lane"))]);
    expect(ranked.every((rank, i) => i === 0 || rank[0] > ranked[i - 1][0] || (rank[0] === ranked[i - 1][0] && rank[1] >= ranked[i - 1][1]))).toBe(true);
    expect(states.filter((state) => state === "landed")).toHaveLength(lanes.flat().filter((state) => state === "landed").length);
    // Groups of five: the step from the fifth tick to the sixth is wider than any step within a group.
    const xs = drawn.map(tickX);
    const steps = xs.slice(1).map((x, i) => Math.round((x - xs[i]) * 100) / 100);
    const within = steps.filter((_, i) => (i + 1) % 5 !== 0), between = steps.filter((_, i) => (i + 1) % 5 === 0);
    expect(new Set(within).size).toBe(1);
    for (const gap of between) expect(gap).toBe(within[0] + 5);
    // The caret marks the queue's next: the first tick in the merge queue.
    const next = drawn.find((tick) => tick.getAttribute("data-state") === "queued");
    expect(app.querySelector("#line .mg-next")?.textContent ?? null).toBe(next ? "next: " + next.getAttribute("data-subject") : null);
    expect(app.querySelectorAll("#line .mg-caret")).toHaveLength(next ? 1 : 0);
    // A lane row is one width whatever its count: five slots, its landed stories folded, at most three stops and "+n".
    expect(app.querySelector("#line .lines-track").style.getPropertyValue("--most")).toBe("5");
    // Rows come in the order the record first names each lane; each is matched to its lane by name.
    for (const row of app.querySelectorAll("#line .line")) {
      const own = lanes[Number(/^Lane (\d+)/.exec(row.querySelector(".name b").textContent)[1]) - 1];
      const open = own.filter((state) => state !== "landed").length;
      const slots = [...row.querySelectorAll(".stops > .seg")];
      expect(slots.length).toBeLessThan(6);
      expect(row.querySelectorAll(".stop-btn")).toHaveLength(Math.min(open, 3));
      expect(row.querySelector(".seg.fold")?.textContent ?? null).toBe(own.length - open ? `${own.length - open} in` : null);
      expect(row.querySelector(".seg.more")?.textContent ?? null).toBe(open > 3 ? `+${open - 3}` : null);
      if (open > 3) {
        // Its accessible name lists exactly the folded open stories: the lane's open ones without a stop.
        const label = row.querySelector(".seg.more").getAttribute("aria-label");
        expect(label.startsWith(`${open - 3} more open: `)).toBe(true);
        const named = label.slice(label.indexOf(": ") + 2).split(/, | and /).sort();
        const lane = row.getAttribute("data-lane");
        const kept = new Set([...row.querySelectorAll(".stop-btn")].map((stop) => stop.getAttribute("data-subject")));
        const folded = drawn.filter((tick) => tick.getAttribute("data-lane") === lane && tick.getAttribute("data-state") !== "landed")
          .map((tick) => tick.getAttribute("data-subject")).filter((subject) => !kept.has(subject)).sort();
        expect(named).toEqual(folded);
      }
    }
  });
}

test("a stopped story's tick is marked, and the count line says how many are stopped", async () => {
  const [{ app }] = await open(delivery([["landed", "stopped", "planned"], ["landed", "stopped"]]));
  const stopped = ticks(app).filter((tick) => tick.getAttribute("data-state") === "stopped");
  expect(stopped).toHaveLength(2);
  for (const tick of stopped) expect(tick.querySelector(".mg-flag")).not.toBeNull();
  expect(ticks(app).filter((tick) => tick.querySelector(".mg-flag"))).toHaveLength(2);
  expect(app.querySelector("#line .mg-tally").textContent).toBe("2 of 5 landed· 2 stopped");
  expect(app.querySelector("#line .mg-stopped").textContent).toBe("· 2 stopped");
  const [{ app: none }] = await open(delivery([["landed", "planned"]]));
  expect(none.querySelector("#line .mg-stopped")).toBeNull();
});

const INTEGRATION = { base: "integrate/app", target: "main", remote: "origin" };
const H = headOf("base");
const merged = [30, "integration.merged", KEY, { data: { target: "main", targetHead: headOf("main-30"), head: H, conflicts: [] } }];
const suite = [31, "integration.suite", KEY, { data: { head: H, result: "passed", seconds: 1200 } }];
const round = [32, "review.round", KEY, { round: 1, data: { head: H }, verdicts: { "lens.outcome-correctness": "approve" } }];
const requested = [33, "integration.requested", KEY, { data: { changeRequest: "https://github.com/acme/app/pull/7", head: H } }];
const rejoined = [34, "integration.landed", KEY, { data: { target: "main", head: headOf("main-final") } }];

test("the rejoin bar names the step it is on at each stage, and bends into the target at the post", async () => {
  const stages = [[[], "main in", ["next", "hollow", "hollow", "hollow"]], [[merged], "suite", ["filled", "next", "hollow", "hollow"]],
    [[merged, suite], "review", ["filled", "filled", "next", "hollow"]], [[merged, suite, round], "change request", ["filled", "filled", "filled", "next"]],
    // Every step in but not rejoined: it waits on its change request.
    [[merged, suite, round, requested], "change request", ["filled", "filled", "filled", "filled"]],
    [[merged, suite, round, requested, rejoined], "done", ["filled", "filled", "filled", "filled"]]];
  for (const [steps, label, states] of stages) {
    const [{ app }] = await open(delivery([["landed", "landed"]], { integration: INTEGRATION, steps }));
    expect([label, app.querySelector("#line .mg-bar-label").textContent]).toEqual([label, "Rejoin main · " + label]);
    expect([label, [...app.querySelectorAll("#line .mg-step")].map((step) => step.getAttribute("data-state"))]).toEqual([label, states]);
    // The target is drawn once, beginning at the post.
    expect(app.querySelectorAll("#line .mg-target")).toHaveLength(1);
    expect(app.querySelector("#line .mg-target").getAttribute("d").startsWith("M" + /^M(\d+)/.exec(app.querySelector("#line .mg-post").getAttribute("d"))[1] + " ")).toBe(true);
  }
  // Once it has rejoined, the status says so and the target is solid; before, it is the edge's colour.
  const [{ app: done }] = await open(delivery([["landed", "landed"]], { integration: INTEGRATION, steps: [merged, suite, round, requested, rejoined] }));
  expect(done.querySelector("#line .mg-step-said").textContent).toBe("Rejoined main.");
  expect(done.querySelector("#line .mg-target").getAttribute("class")).toBe("mg-target solid");
  const [{ app: open_ }] = await open(delivery([["landed", "queued"]], { integration: INTEGRATION }));
  expect(open_.querySelector("#line .mg-target").getAttribute("class")).toBe("mg-target");
  // Straight to the base: the post stands at the end of the ticks, with no bar and no target.
  const [{ app: plain }] = await open(delivery([["landed", "queued"]]));
  expect(plain.querySelectorAll("#line .mg-step, #line .mg-target, #line .mg-bar-label")).toHaveLength(0);
  const postX = Number(/^M(\d+)/.exec(plain.querySelector("#line .mg-post").getAttribute("d"))[1]);
  expect(postX).toBeGreaterThan(Math.max(...ticks(plain).map(tickX)));
  expect(plain.querySelector("#line .mg-trunk").getAttribute("d").endsWith("H" + postX)).toBe(true);
  expect(plain.textContent).not.toContain("Before it rejoins");
});

test("the drawing's name states the landed, met and rejoin counts, and it is a group whose parts are each reached", async () => {
  const finish = [{ id: "stories", text: "The stories land", met: true }, { id: "suite", text: "The local suite passes", met: false }];
  const [{ app }] = await open(delivery([["landed", "queued"], ["review"]], { integration: INTEGRATION, steps: [merged], finish }));
  const svg = app.querySelector("#line .line-merge");
  expect(svg.getAttribute("role")).toBe("group");
  expect(svg.getAttribute("aria-label")).toBe("Every lane merges into one main line: 1 of 3 pieces landed, 1 of 2 finish lines met, then rejoins main: 1 of 4 steps done");
  const reachable = [...svg.querySelectorAll(".mg-tick, .mg-step, .mg-square")];
  expect(reachable).toHaveLength(3 + 4 + 2);
  for (const node of reachable) expect([node.getAttribute("class"), node.getAttribute("tabindex"), Boolean(node.getAttribute("aria-label"))])
    .toEqual([node.getAttribute("class"), "0", true]);
  expect([...svg.querySelectorAll(".mg-square")].map((square) => [square.getAttribute("class"), square.getAttribute("aria-label")]))
    .toEqual([["mg-square met", "The stories land, met"], ["mg-square unmet", "The local suite passes, not met"]]);
  expect(svg.querySelector(".mg-square ~ text.mg-count").textContent).toBe("1/2");
});

test("focusing a tick, a step or a square names it and lights a tick's lane; hovering a lane lights its ticks", async () => {
  const finish = [{ id: "stories", text: "The stories land", met: false }];
  const doc = delivery([["landed", "queued"], ["review"]], { integration: INTEGRATION, finish });
  const focus = (selector) => ({ run: (window) => window.document.querySelector(selector).focus() });
  const blur = (selector) => ({ run: (window) => window.document.querySelector(selector).blur() });
  const hover = (selector, kind) => ({ run: (window) => window.document.querySelector(selector).dispatchEvent(new window.Event(kind)) });
  const [, onTick, offTick, onStep, onSquare, onLane, offLane] = await open(doc, [
    focus('.mg-tick[data-subject="APP-3"]'), blur('.mg-tick[data-subject="APP-3"]'), focus(".mg-step"), focus(".mg-square"),
    hover('.line[data-lane="0"]', "mouseenter"), hover('.line[data-lane="0"]', "mouseleave")]);
  const tip = (snap) => snap.app.querySelector("#line .mg-tip");
  const lit = (snap) => [...snap.app.querySelectorAll("#line .lines-track.lit [data-lane].on")].map((node) => node.getAttribute("class").split(" ")[0] + ":" + node.getAttribute("data-lane"));
  // A tick names its story and state, opens its panel, and lights its lane's row, turn and ticks.
  expect(tip(onTick).hidden).toBe(false);
  expect(tip(onTick).textContent).toBe("APP-3 · in reviewWork APP-3");
  expect(lit(onTick).sort()).toEqual(["line:1", "mg-tick:1", "mg-turn:1"]);
  expect(onTick.app.querySelector("#detail .d1")?.textContent).toBe("Work APP-3");
  expect(onTick.app.querySelector('.mg-tick[data-subject="APP-3"]').getAttribute("aria-pressed")).toBe("true");
  expect([tip(offTick).hidden, lit(offTick)]).toEqual([true, []]);
  // A step names itself and its status; a square names its finish line. Neither belongs to a lane.
  expect(tip(onStep).textContent).toBe("Main in · nowNot yet: the lead merges main in and records it.");
  expect(lit(onStep)).toEqual([]);
  expect(tip(onSquare).textContent).toBe("Finish line · not metThe stories land");
  // Hovering a lane's row lights its row, its turn and every tick of it, and dims the rest.
  expect(lit(onLane).sort()).toEqual(["line:0", "mg-tick:0", "mg-tick:0", "mg-turn:0"]);
  expect(lit(offLane)).toEqual([]);
  // A tick is a button: Enter on it opens its panel, as clicking it does.
  const [, pressed] = await open(doc, [{ run: (window) => {
    const tick = window.document.querySelector('.mg-tick[data-subject="APP-1"]');
    tick.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  } }]);
  expect(pressed.app.querySelector("#detail .d1")?.textContent).toBe("Work APP-1");
});

test("the piece being checked keeps its stop when its lane has more than three open stories, and the rest fold", async () => {
  const [{ app }] = await open(delivery([["stopped", "queued", "queued", "checking"]]));
  const row = app.querySelector("#line .line");
  // The checking piece is the lane's fourth open story in the queue's order; it keeps a stop, so the stream still fires.
  expect([...row.querySelectorAll(".stop-btn")].map((stop) => [stop.getAttribute("data-subject"), stop.className.replace("stop-btn ", "")]))
    .toEqual([["APP-1", "held"], ["APP-2", "ready"], ["APP-4", "now"]]);
  expect([row.querySelector(".seg.more").textContent, row.querySelector(".seg.more").getAttribute("aria-label")]).toEqual(["+1", "1 more open: APP-3"]);
});

test("only the step the bar is on reads now; a step after it reads later, even one already marked next", async () => {
  const failed = [31, "integration.suite", KEY, { data: { head: H, result: "failed", seconds: 1200 } }];
  const doc = delivery([["landed"]], { integration: INTEGRATION, steps: [merged, failed] });
  const focus = (index) => ({ run: (window) => window.document.querySelectorAll(".mg-step")[index].focus() });
  const [, first, second, third, fourth] = await open(doc, [focus(0), focus(1), focus(2), focus(3)]);
  const head = (snap) => snap.app.querySelector("#line .mg-tip b").textContent;
  expect([first, second, third, fourth].map(head)).toEqual(["Main in · done", "Suite · now", "Review · later", "Change request · later"]);
});

test("the caret passes over a hand-over the lead hasn't accepted, which ticks after the ready ones", async () => {
  const [{ app: only }] = await open(delivery([["landed", "pending", "building"]]));
  expect([only.querySelector("#line .mg-caret"), only.querySelector("#line .mg-next")]).toEqual([null, null]);
  const [{ app: both }] = await open(delivery([["pending", "queued"]]));
  // The pending one comes first in the lane, the ready one first on the line, and the caret is on the ready one.
  expect(ticks(both).map((tick) => tick.getAttribute("data-subject"))).toEqual(["APP-2", "APP-1"]);
  expect(both.querySelector("#line .mg-next").textContent).toBe("next: APP-2");
});

test("a closed delivery ticks every landed story before every one that never landed", async () => {
  const doc = delivery([["planned", "landed"], ["queued", "landed"]], { steps: [[40, "delivery.closed", null]] });
  const [{ app }] = await open(doc);
  const states = ticks(app).map((tick) => tick.getAttribute("data-state"));
  expect(states).toEqual(["landed", "landed", "unlanded", "unlanded"]);
});

test("the main line is solid to the bar once every story has landed, and through the bend once it has rejoined", async () => {
  const trunk = (app) => [...app.querySelectorAll("#line .mg-trunk")].map((path) => [path.getAttribute("class"), path.getAttribute("d")]);
  const barX = (app) => Number(app.querySelector("#line .mg-step").getAttribute("x"));
  const [{ app: open_ }] = await open(delivery([["landed", "queued"]], { integration: INTEGRATION }));
  expect(trunk(open_).map(([cls]) => cls)).toEqual(["mg-trunk open"]);
  const [{ app: allIn }] = await open(delivery([["landed", "landed"]], { integration: INTEGRATION, steps: [merged] }));
  expect(trunk(allIn).map(([cls]) => cls)).toEqual(["mg-trunk open", "mg-trunk"]);
  expect(trunk(allIn)[1][1]).toBe(`M0 ${/ (\d+)H/.exec(trunk(allIn)[1][1])[1]}H${barX(allIn) - 10}`);
  const [{ app: done }] = await open(delivery([["landed", "landed"]], { integration: INTEGRATION, steps: [merged, suite, round, requested, rejoined] }));
  const drawn = trunk(done);
  expect(drawn.map(([cls]) => cls)).toEqual(["mg-trunk open", "mg-trunk", "mg-trunk"]);
  // The solid run reaches the bend, and the bend itself is solid, ending at the post on the target.
  const post = /^M(\d+) (\d+(?:\.\d+)?)V/.exec(done.querySelector("#line .mg-post").getAttribute("d"));
  expect(drawn[1][1].endsWith(`H${Number(post[1]) - 30}`)).toBe(true);
  expect(drawn[2][1].endsWith(` ${post[1]} ${Number(post[2]) + 20}`)).toBe(true);
  // Rejoined, every step says it is done.
  expect([...done.querySelectorAll("#line .mg-step")].map((step) => step.getAttribute("aria-label")))
    .toEqual(["Main in: Done.", "Suite: Done.", "Review: Done.", "Change request: Done."]);
});

test("a tick opens its panel on Space and on a click with no focus, and a stop's focus lights its lane", async () => {
  const doc = delivery([["landed", "queued"], ["review"]]);
  const key = (subject, value) => ({ run: (window) => window.document.querySelector(`.mg-tick[data-subject="${subject}"]`)
    .dispatchEvent(new window.KeyboardEvent("keydown", { key: value, bubbles: true })) });
  const click = (subject) => ({ run: (window) => window.document.querySelector(`.mg-tick[data-subject="${subject}"]`)
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true })) });
  const [, spaced, clicked, focused] = await open(doc, [key("APP-1", " "), click("APP-3"),
    { run: (window) => window.document.querySelector('.stop-btn[data-subject="APP-2"]').focus() }]);
  expect(spaced.app.querySelector("#detail .d1").textContent).toBe("Work APP-1");
  expect(clicked.app.querySelector("#detail .d1").textContent).toBe("Work APP-3");
  const lit = [...focused.app.querySelectorAll("#line .lines-track.lit [data-lane].on")].map((node) => node.getAttribute("class").split(" ")[0]).sort();
  expect(lit).toEqual(["line", "mg-tick", "mg-tick", "mg-turn"]);
});

test("under reduced motion a story that lands just lands; a first open stop after the fold isn't drawn incoming", async () => {
  const before = delivery([["landed", "queued", "building"]]);
  const after = delivery([["landed", "landed", "building"]]);
  const motion = (reduced) => openPage(pageWithRuns(before, {}, { config: CONFIG }), { now: NOW, store: { read: "pending" },
    motion: { reduced }, steps: [{ push: before }, { push: after }] });
  const tickOf = (snap) => snap.app.querySelector('.mg-tick[data-subject="APP-2"]').getAttribute("class");
  const foldOf = (snap) => snap.app.querySelector("#line .seg.fold").className;
  const moving = await motion(false), still = await motion(true);
  expect([tickOf(moving[2]), foldOf(moving[2])]).toEqual(["mg-tick landed landing", "seg fold grew"]);
  expect([tickOf(still[2]), foldOf(still[2])]).toEqual(["mg-tick landed", "seg fold"]);
  // The open stop next to the fold is reached along the rule, not drawn coming in; one with nothing before it is.
  expect(still[2].app.querySelector("#line .stops > .seg:not(.fold)").className).toBe("seg");
  const [{ app: alone }] = await open(delivery([["building"]]));
  expect(alone.querySelector("#line .stops > .seg").className).toBe("seg incoming");
});

test("rejoining, the stream down the main line stops short of the bar, and the track keeps room for what hangs below", async () => {
  const doc = () => {
    const shown = delivery([["landed", "checking"]], { integration: INTEGRATION, steps: [merged] });
    shown.state.meta.checking = { now: { subject: "APP-2", steps: [{ id: "suite", state: "running" }] }, next: [] };
    return shown;
  };
  const WIDTH = 1200;
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
  const svg = drawn.app.querySelector("#line .line-merge");
  const width = Number(svg.getAttribute("width")), barX = Number(svg.querySelector(".mg-step").getAttribute("x"));
  const trunk = drawn.app.querySelector(".line-flow path.flow-trunk").getAttribute("d");
  expect(trunk.endsWith(`H${WIDTH - width + barX - 10}`)).toBe(true);
  // One row: 46 of drawing for it, and the bar's status and the target hang below that, kept as room under the track.
  const track = drawn.app.querySelector("#line .lines-track");
  expect(Number(svg.getAttribute("height"))).toBeGreaterThan(46);
  expect(track.style.getPropertyValue("--merge-below")).toBe(`${Number(svg.getAttribute("height")) - 46 + 16}px`);
});
