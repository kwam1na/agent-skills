// The line map's behaviour, ported from the suite of the TypeScript page it was taken from.
process.env.TZ = "UTC";

import { spawnSync } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CORPUS, RENDER, REPLAYED_RUNS, ROOT, block, embedded, expect, openPage, pageWithRuns, readJson, renderIn, repository, summarize } from "./support.mjs";
import { FIX, readings, reportView } from "./round-reports.mjs";

const SOURCE = path.join(CORPUS, "source");
const NOW = Date.parse("2026-10-22T05:10:00Z");

/** The configuration the suite renders under: one tracker, one forge, the replayed runs and two named lenses. */
export const CONFIG = {
  spec: "delivery-views-config/1",
  tracker: { name: "Tracker", issueUrl: "https://tracker.example/issue/{key}", keyPattern: "^PAY-[0-9]+$" },
  forges: [{ name: "GitHub", repository: "acme/shop", pullUrl: "https://github.com/{repository}/pull/{number}", commitUrl: "https://github.com/{repository}/commit/{sha}" }],
  runs: REPLAYED_RUNS,
  lenses: { "lens.outcome-correctness": { name: "Correctness" }, "lens.adversarial-testing": { name: "Testing" } },
};

/** The line map for a document and run summaries, under the suite's configuration unless another is given. */
function render(document, runs = {}, options = {}) {
  return pageWithRuns(document, runs, { config: CONFIG, ...options });
}

// Each reviewer chip on the round card as [its name, the verdict it states only where it differs from the round's].
function reviewers(scope) {
  return [...scope.querySelectorAll(".run-card .run-lens")].map((lens) => [lens.querySelector(".run-lens-name")?.textContent,
    lens.querySelector(".run-lens-verdict")?.textContent ?? null]);
}
const HOSTILE = "</script><img src=x onerror=alert(1)> <b>bold</b> next line & \"quoted\" </script x </SCRIPT/ <!--<script>";

/** The fixture record as the render wrapper hands it to a renderer. */
function fixtureDocument() {
  const state = readJson(path.join(SOURCE, "state.json"));
  const journal = readFileSync(path.join(SOURCE, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return {
    state,
    journal,
    status: {
      source: "derived",
      text: "Checking Retry card payments once on a dropped connection, 3 of 6 checks done · last landed PAY-2358 at 2026-10-22T04:26:00Z · blocked: backend (waiting on the dev backend lease) · stopped: PAY-2360 · next: PAY-2363",
    },
    focus: {},
    stats: {
      "PAY-2358": { rounds: 2, roundsSource: "reported", waitSeconds: 120, checkToMergeSeconds: 840, totalSeconds: 4860, outcome: "merged" },
      "PAY-2361": { rounds: 3, roundsSource: "reported", waitSeconds: 60 },
    },
    estimates: { "PAY-2363": { position: 1, expectedStart: "2026-10-22T05:19:22Z" } },
  };
}

/**
 * The page run as a browser would, with the clock fixed and, when the scenario gives one, a fake live store in
 * place; each snapshot is { app, text, focused, source }.
 */
function open(document, scenario = {}, page = render(document)) {
  return openPage(page, { now: new Date(NOW).toISOString(), store: false, ...scenario });
}

test("opts in to the live server and loads nothing but its two faces", async () => {
  const page = render(fixtureDocument());
  const head = page.slice(0, page.indexOf("</head>"));
  expect(head).toContain('<meta name="delivery-state-live">');
  expect(page.indexOf('<meta name="delivery-state-live">')).toBeLessThan(page.indexOf("<script"));
  expect(page).not.toMatch(/\ssrc=|@import|url\(/);
  const hrefs = page.match(/\shref="[^"]*"/g);
  expect(hrefs).toHaveLength(1);
  expect(hrefs[0]).toMatch(/^ href="https:\/\/fonts\.googleapis\.com\/css2\?/);
  // The only other addresses are the links the renderer worked out from the configuration, for ticket ids and
  // change requests, each the configured tracker's or forge's template filled in.
  const links = block(page, "delivery-links");
  const linked = [...Object.values(links.issues), ...Object.values(links.changes).map((change) => change.href), ...Object.values(links.commits)];
  expect(linked.length).toBeGreaterThan(10);
  expect(linked.every((href) => /^https:\/\/(tracker\.example\/issue\/PAY-[0-9]+|github\.com\/acme\/shop\/pull\/[0-9]+)$/.test(href))).toBe(true);
  const addresses = [...new Set(page.match(/https:\/\/[a-z.]+\/[^"\s]*/g))];
  expect(addresses.filter((address) => !address.startsWith("https://fonts.googleapis.com/css2?")).sort())
    .toEqual([...new Set(linked)].sort());
});

test("draws the run as a line map, chapter by chapter", async () => {
  const [{ app }] = await open(fixtureDocument());
  const chapter = (id) => app.querySelector(`#${id}`);
  expect([...app.querySelectorAll(".rail a")].map((link) => link.getAttribute("aria-label"))).toEqual(
    ["Now", "The line", "Who", "What's left", "What happened", "Parked"]);

  // Now: how far the run has got, what is being checked, and what needs attention.
  const now = chapter("now");
  expect(now.querySelector(".kicker .headline")?.textContent).toBe("Checkout resilience");
  // The tracker item the delivery is listed under opens in the tracker.
  expect(now.querySelector(".kicker a")?.getAttribute("href")).toBe("https://tracker.example/issue/PAY-2354");
  expect(now.querySelector("h1")?.textContent).toBe("1 of 8 pieces have landed.");
  expect(now.querySelector(".say")?.textContent).toBe("Last landed: Hold the cart through a payment redirect, 44m ago.");
  const checking = now.querySelector(".checking").textContent;
  expect(checking).toContain("Being checked now");
  expect(checking).toContain("Retry card payments once on a dropped connection");
  expect([...now.querySelectorAll(".ck-step")].map((step) => step.className.replace("ck-step ", ""))).toEqual(
    ["passed", "passed", "reused", "running", "pending", "pending"]);
  expect(checking).toContain("Running storefront build · 2m 30s of about 4m");
  expect(checking).not.toContain("longer than usual");
  expect(checking).toContain("3 of 6 checks done · started 6m ago");
  expect(checking).toContain("Up next: Keep the order number on the receipt page · Record the payment provider's reference");
  const attention = now.querySelector(".attention").textContent;
  expect(attention).toContain("Show a calm message when the card is declined stopped");
  expect(attention).toContain("checkout end-to-end failed: declined card shows the retry button, declined card keeps the cart");
  expect(attention).toContain("Backend is blocked");
  expect(attention).toContain("Waiting on the dev backend lease");
  expect(attention).toContain("its change request closed without merging");
  expect(attention).toContain("The forge is not answering");
  expect(now.querySelector(".since")?.textContent).toContain("Saved view · updated just now · base aaaaaaa");

  // The line: one row per lane, a stop per piece, drawn by where the piece stands.
  const line = chapter("line");
  expect([...line.querySelectorAll(".line .name b")].map((name) => name.textContent)).toEqual(
    ["Payments", "Storefront", "Backend", "Not yet assigned"]);
  const stops = Object.fromEntries([...line.querySelectorAll(".stop-btn")].map((stop) => [stop.getAttribute("data-subject"), stop.className.replace("stop-btn ", "").trim()]));
  expect(stops).toEqual({
    "PAY-2358": "merged", "PAY-2361": "now", "PAY-2360": "held", "PAY-2362": "now", "PAY-2364": "active",
    "PAY-2363": "ready", "PAY-2365": "active", "PAY-2366": "later",
  });
  // The detail starts on what is being checked, in the tracker's own words.
  const detail = line.querySelector("#detail").textContent;
  expect(detail).toContain("Retry card payments once on a dropped connection");
  // The building lane labels the panel; under the title, the ticket, the change request and where the piece stands.
  expect(line.querySelector("#detail .d-owner")?.textContent).toBe("Payments lane");
  expect(line.querySelector("#detail .d-meta")?.textContent).toBe("PAY-2361 · #1106 · Being checked before merge · Checking since 6 minutes ago");
  expect(line.querySelector('#detail a.pr-num')?.getAttribute("href")).toBe("https://github.com/acme/shop/pull/1106");
  expect(line.querySelector('.stop-label a[data-ticket="PAY-2361"]')?.getAttribute("href")).toBe("https://tracker.example/issue/PAY-2361");
  expect(detail).toContain("A dropped connection during card payment retries once, without charging twice.");
  expect(detail).toContain("3 review rounds");

  // Who: each lane, its state and what it is on, and the leases held.
  const who = chapter("who");
  expect(who.querySelector("h2")?.textContent).toBe("3 of 4 lanes are working right now.");
  expect([...who.querySelectorAll(".chip")].map((chip) => chip.textContent)).toEqual(["Handed over", "In review", "Blocked", "Standing by"]);
  expect(who.textContent).toContain("waiting on the dev backend lease");
  expect(who.querySelector(".leases")?.textContent).toBe("dev-backend held by lead for 25m");

  // What's left: the pieces to go, by lane, and the finish line.
  const left = chapter("left");
  expect(left.querySelector(".left-number")?.textContent).toBe("7pieces to go");
  expect(left.querySelectorAll(".dots i").length).toBe(8);
  expect(left.querySelectorAll(".dots i.open").length).toBe(7);
  expect([...left.querySelectorAll(".after div")].map((line) => line.textContent)).toEqual([
    "MetA dropped connection during payment never charges twice",
    "NextEvery payment state shows a clear next step",
    "ThenThe webhook replay recovers a missed payment",
  ]);

  // What happened: newest first, marked by kind, eight at a time.
  const happened = chapter("happened");
  const entries = [...happened.querySelectorAll(".feed li")];
  expect(entries).toHaveLength(8);
  expect(entries[0].textContent).toContain("Checking Retry card payments once on a dropped connection");
  const landed = entries.find((entry) => entry.textContent?.includes("Merged PAY-2358"));
  expect(landed.querySelector(".g")?.className).toBe("g merge");
  expect(landed.textContent).toContain("Landed · Payments · PAY-2358");
  expect(happened.querySelector(".more")?.textContent).toBe("Show all 10");

  // Parked: the follow-ups.
  expect(chapter("parked").textContent).toContain("Measure checkout drop-off by payment state");
});

test("says when the checks being run were chosen by the lead, with the lead's reason", async () => {
  const plain = (await open(fixtureDocument()))[0].app.querySelector(".checking").textContent;
  expect(plain).not.toContain("chosen by the lead");
  const document = fixtureDocument();
  const subject = document.state.meta.checking.now.subject;
  const item = document.state.queue.find((entry) => entry.subject === subject);
  item.checkSelection = { by: "lead", head: item.head, reason: "only the retry path changed" };
  const [{ app }] = await open(document);
  expect(app.querySelector(".checking").textContent).toContain(
    "Checks chosen by the lead, not the usual selection: only the retry path changed");
  item.checkSelection = { by: "provider" };
  const [{ app: provider }] = await open(document);
  expect(provider.querySelector(".checking").textContent).not.toContain("chosen by the lead");
});

test("shows a ticket's own title and summary when its id is hovered, wherever the id appears", async () => {
  const document = fixtureDocument();
  document.state.tickets["PAY-2360"] = { title: "Calm decline message", summary: HOSTILE };
  const [start, onStop, left, onFeed, onParked, onHostile] = await open(document, {
    steps: [
      { hover: '#line .stop-label [data-ticket="PAY-2361"]' },
      { leave: '#line .stop-label [data-ticket="PAY-2361"]' },
      { hover: '#happened .who [data-ticket="PAY-2358"]' },
      { hover: '#parked [data-ticket="PAY-2370"]' },
      { hover: '#line .stop-label [data-ticket="PAY-2360"]' },
    ],
  });
  const card = (snapshot) => snapshot.app.querySelector(".issue-card");
  expect(card(start).hasAttribute("hidden")).toBe(true);
  // A ticket the tracker summarised: its id, its title, then its summary.
  expect(card(onStop).hasAttribute("hidden")).toBe(false);
  expect([...card(onStop).children].map((node) => node.textContent)).toEqual([
    "PAY-2361", "Retry card payments once on a dropped connection", "A dropped connection during card payment retries once, without charging twice.",
  ]);
  expect(card(left).hasAttribute("hidden")).toBe(true);
  // Without a tracker summary, the record's own title.
  expect([...card(onFeed).children].map((node) => node.textContent)).toEqual(["PAY-2358", "Hold the cart through a payment redirect"]);
  expect([...card(onParked).children].map((node) => node.textContent)).toEqual(["PAY-2370", "Measure checkout drop-off by payment state"]);
  // The tracker's words stay text.
  expect(card(onHostile).querySelector(".ic-s")?.textContent).toBe(HOSTILE);
  expect(card(onHostile).querySelector("img")).toBeNull();
  // Every place an id appears is a link to its issue, so it can be focused to show its card.
  const ids = [...onStop.app.querySelectorAll("[data-ticket]")];
  expect(ids.length).toBeGreaterThan(10);
  expect(ids.every((id) => id.getAttribute("href") === `https://tracker.example/issue/${id.getAttribute("data-ticket")}`)).toBe(true);
});

test("shows a long list of checks as a bar when the record has no check times, with the running one named", async () => {
  const document = fixtureDocument();
  document.state.meta.checking.now.steps = Array.from({ length: 36 }, (_, index) => ({
    label: `shop.check.${index}`, state: index < 4 ? "passed" : index === 4 ? "running" : "pending",
  }));
  document.state.meta.checking.now.steps[4].startedAtUnreadable = true;
  const [{ app }] = await open(document);
  expect(app.querySelectorAll(".ck-step")).toHaveLength(0);
  expect(app.querySelector(".run-line")).toBeNull();
  expect(app.querySelectorAll(".ck-bar span")).toHaveLength(36);
  document.state.meta.checking.now.steps[4].startedAt = "2026-10-22T05:09:00Z";
  const [{ app: timed }] = await open(document);
  expect(timed.querySelector(".checking").textContent).toContain("Running shop.check.4 · 1m");
  expect(timed.querySelector(".checking").textContent).toContain("4 of 36 checks done");
});

test("puts failed checks first, says the piece won't merge from this run, and sets the lead's notes beside them", async () => {
  const document = fixtureDocument();
  const ids = Array.from({ length: 20 }, (_, index) => `shop.check.${(0x2e5d2ef738e98520n + BigInt(index)).toString(16)}`);
  const states = ids.map((_, index) => (index === 3 || index === 9 ? "failed" : index === 5 ? "reused" : index < 12 ? "passed" : index === 12 ? "running" : "pending"));
  // As the runner times them: one after another from the run's start, the second failure a long one.
  const at = (minutes) => new Date(Date.parse("2026-10-22T04:30:00Z") + minutes * 60000).toISOString();
  document.state.meta.checking.now.startedAt = at(0);
  document.state.meta.checking.now.steps = ids.map((label, index) => ({
    label, state: states[index],
    ...(index < 12 ? { startedAt: at(index), finishedAt: at(index === 9 ? 19 : index + 1) } : index === 12 ? { startedAt: "2026-10-22T04:50:00Z" } : {}),
  }));
  document.state.queue[2].checks = ids.map((id, index) => ({
    id, state: states[index], ...(states[index] === "failed" ? { log: `logs/00${index}-${id}.log` } : {}), ...(index === 9 ? { failures: ["harness-behavior stop.log"], detail: "exit 1 after 2 retries" } : {}),
  }));
  document.journal.push(
    { at: "2026-10-22T05:06:00Z", id: 21, kind: "blocker.recorded", lane: null, subject: "PAY-2361", summary: `Already failing on main: ${ids[3]}, the motion inventory test.` },
    { at: "2026-10-22T05:06:30Z", id: 22, kind: "decision.recorded", lane: null, subject: "PAY-2361", summary: `Load flake, not a code failure: \`${ids[9]}\`.` },
    { at: "2026-10-22T05:07:00Z", id: 23, kind: "decision.recorded", lane: null, subject: "PAY-2361", summary: "Verdict: this will not merge from this run." },
    { at: "2026-10-22T05:07:30Z", id: 24, kind: "decision.recorded", lane: null, subject: "PAY-2363", summary: `About another piece: ${ids[3]}.` },
    // Names a longer id that starts with a failed one: it is not about that check.
    { at: "2026-10-22T04:20:00Z", id: 26, kind: "blocker.recorded", lane: null, subject: "PAY-2361", summary: `From an earlier run: ${ids[3]}.` },
    { at: "not a time", id: 27, kind: "blocker.recorded", lane: null, subject: "PAY-2361", summary: `Undated: ${ids[3]}!` },
    { at: "2026-10-22T05:08:00Z", id: 25, kind: "decision.recorded", lane: null, subject: "PAY-2361", summary: `Unrelated: ${ids[9]}.publishing passed.` },
  );
  const [{ app }] = await open(document);
  const checking = app.querySelector(".checking");
  // The verdict comes from the check states alone.
  expect(checking.querySelector(".ck-verdict")?.textContent).toBe("Won't merge from this run. 2 failed · 8 still to run");
  // The run on the clock: a stretch and a stem per started check, failed stops numbered as the list,
  // the running one at now with its time so far, and the waiting ones trailing off.
  const line = checking.querySelector("svg.run-line");
  expect(line.querySelectorAll(".rl-seg")).toHaveLength(13);
  expect(line.querySelectorAll(".rl-stem")).toHaveLength(13);
  expect([...line.querySelectorAll(".rl-num")].map((number) => number.textContent)).toEqual(["1", "2"]);
  // Clock skew: an end before its start, and a running check that seems to start after now, draw as points.
  const skewed = JSON.parse(JSON.stringify(document));
  skewed.state.meta.checking.now.steps[2].finishedAt = "2026-10-22T04:20:00Z";
  skewed.state.meta.checking.now.steps[12].startedAt = "2026-10-22T06:00:00Z";
  const [{ app: skewedApp }] = await open(skewed);
  const values = [...skewedApp.querySelectorAll(".run-line *")].flatMap((node) => [...node.attributes].map((attribute) => attribute.value));
  expect(values.filter((value) => /NaN|Infinity/.test(value))).toEqual([]);
  expect(skewedApp.querySelectorAll(".run-line .rl-stem")).toHaveLength(13);
  // A run that has not started by the viewer's clock has no line to draw.
  const ahead = JSON.parse(JSON.stringify(document));
  ahead.state.meta.checking.now.startedAt = "2026-10-22T05:10:00Z";
  const [{ app: aheadApp }] = await open(ahead);
  expect(aheadApp.querySelector(".run-line")).toBeNull();
  expect(aheadApp.querySelector(".ck-bar")).not.toBeNull();
  // A failed check with no time keeps its number in the list; the drawn ones keep theirs.
  const untimed = JSON.parse(JSON.stringify(document));
  delete untimed.state.meta.checking.now.steps[3].startedAt;
  delete untimed.state.meta.checking.now.steps[3].finishedAt;
  const [{ app: untimedApp }] = await open(untimed);
  expect([...untimedApp.querySelectorAll(".run-line .rl-num")].map((number) => number.textContent)).toEqual(["2"]);
  expect([...checking.querySelectorAll(".ck-fails .g.num")].map((number) => number.textContent)).toEqual(["1", "2"]);
  expect(line.querySelector(".rl-time.running")?.textContent).toBe("20m so far");
  expect([...line.querySelectorAll(".rl-tick")].map((tick) => tick.textContent)).toContain("7 waiting");
  expect(line.getAttribute("aria-label")).toContain("2 failed, 7 waiting");
  // Failed first, each with its log, its failing tests and the lead's note on it; the rest are counts.
  const failures = [...checking.querySelectorAll(".ck-fails li")];
  expect(failures.map((failure) => failure.querySelector(".ck-f")?.textContent)).toEqual([`Failed: ${ids[3]}`, `Failed: ${ids[9]}`]);
  expect(failures[0].querySelector(".ck-log")?.textContent).toBe(`Log: logs/003-${ids[3]}.log`);
  expect([...failures[0].querySelectorAll(".ck-note")].map((note) => note.textContent)).toEqual([`▲ Already failing on main: ${ids[3]}, the motion inventory test.`]);
  expect(failures[1].textContent).toContain("Failing: harness-behavior stop.log");
  expect(failures[1].textContent).toContain("exit 1 after 2 retries");
  // Notes from before this run, or with no readable time, are left out.
  expect(checking.textContent).not.toContain("From an earlier run");
  expect(checking.textContent).not.toContain("Undated");
  expect([...failures[1].querySelectorAll(".ck-note")].map((note) => note.textContent)).toEqual([`◆ Load flake, not a code failure: \`${ids[9]}\`.`]);
  // A note on the piece that names no failed check follows the list; another piece's note stays out.
  expect([...checking.querySelectorAll(".ck-more .ck-note")].map((note) => note.textContent)).toEqual([
    "◆ Verdict: this will not merge from this run.", `◆ Unrelated: ${ids[9]}.publishing passed.`]);
  expect(checking.textContent).not.toContain("About another piece");
  expect(checking.querySelectorAll(".ck-step")).toHaveLength(0);
  expect(checking.textContent).toContain("9 passed, 1 reused, 2 failed, 1 running, 7 waiting");
  // A running check with no usual time shows its time so far, without alarm.
  const running = checking.querySelector(".ck-run");
  expect(running.textContent).toBe(`Running ${ids[12]} · 20m so far`);
  expect(running.className).toBe("ck-run");
});

test("matches a note to a check whose name ends in punctuation", async () => {
  const document = fixtureDocument();
  document.state.meta.checking.now.steps[1] = { label: "lint(webapp)", state: "failed" };
  document.journal.push({ at: "2026-10-22T05:06:00Z", id: 50, kind: "decision.recorded", lane: null, subject: "PAY-2361", summary: "Flaky: lint(webapp) passes alone" });
  const [{ app }] = await open(document);
  expect(app.querySelector(".ck-fails li .ck-note")?.textContent).toBe("◆ Flaky: lint(webapp) passes alone");
});

test("drops the chips for the failed-first list even when there are only a few checks", async () => {
  const document = fixtureDocument();
  document.state.meta.checking.now.steps[1].state = "failed";
  const [{ app }] = await open(document);
  expect(app.querySelectorAll(".ck-step")).toHaveLength(0);
  expect(app.querySelectorAll(".ck-fails li")).toHaveLength(1);
});

test("says when a running check has gone past its usual time", async () => {
  const document = fixtureDocument();
  document.state.meta.checking.now.steps[4].startedAt = "2026-10-22T05:00:00Z";
  const [{ app }] = await open(document);
  const running = app.querySelector(".ck-run");
  expect(running.textContent).toBe("Running storefront build · 10m of about 4m, longer than usual");
  expect(running.className).toContain("over");
});

test("never lets record text become markup", async () => {
  const document = fixtureDocument();
  document.state.meta.headline = HOSTILE;
  document.state.queue[0].title = HOSTILE;
  document.state.lanes[0].focus = HOSTILE;
  document.state.lanes[0].title = HOSTILE;
  document.journal[0].summary = HOSTILE;
  // The tracker's own words win over the record's titles, so they are made hostile too.
  document.state.tickets["PAY-2361"] = { title: HOSTILE, summary: HOSTILE };
  const page = render(document);
  expect(page).not.toContain("</script><img");
  expect(page).not.toContain("\u2028");
  expect(page).not.toContain("\u2029");
  // The embedded record holds no raw "<" at all, so no spelling of a closing tag or comment can end it.
  expect(embedded(document)).not.toContain("<");
  const [{ app, text }] = await open(document);
  expect(app.querySelector("img")).toBeNull();
  // The hostile <b>bold</b> never becomes an element of its own.
  expect([...app.querySelectorAll("b")].filter((node) => node.textContent === "bold")).toHaveLength(0);
  expect(app.querySelector("#now .kicker .headline")?.textContent).toBe(HOSTILE);
  expect(text).toContain("</script><img src=x onerror=alert(1)>");
  expect(JSON.parse(embedded({ text: HOSTILE }))).toEqual({ text: HOSTILE });
});

test("shows a stop's run in its detail: rounds, verdicts, findings, waits and timings, as runs view gives them", async () => {
  const view = readJson(path.join(SOURCE, "run-view.json"));
  const summary = summarize(view, { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 });
  // PAY-2361 is being checked, so its detail is the one shown first; its queue element names run-7.
  const [{ app }] = await open(fixtureDocument(), {}, render(fixtureDocument(), { "run-7": summary }));
  const run = app.querySelector("#detail .d-run");
  expect(run).not.toBeNull();
  const text = run.textContent;
  expect(run.querySelector(".run-story")?.textContent).toBe("Approved on round 2 · 1 serious note, 2 moderate notes, 1 minor note raised");
  const beads = [...run.querySelectorAll(".run-track .run-bead")];
  expect(beads.map((bead) => [bead.className, bead.querySelector(".run-dot")?.textContent, bead.querySelector(".run-badge")?.textContent ?? null])).toEqual([
    ["run-bead is-changes", "1", "3"],
    ["run-bead is-approve", "2", "1"],
  ]);
  expect(beads[0].querySelector(".run-badge")?.className).toBe("run-badge is-p1");
  expect(beads[0].querySelector("time")?.getAttribute("datetime")).toBe("2026-10-22T04:40:00.000Z");
  expect(beads[1].querySelector("time")?.textContent).toBe("18 min later");
  // The card opens on the latest round; picking the first bead swaps it in.
  expect(run.querySelector(".run-card .run-card-head b")?.textContent).toBe("Round 2");
  const [, picked] = await open(fixtureDocument(), { steps: [{ click: "#detail .run-pick" }] }, render(fixtureDocument(), { "run-7": summary }));
  const card = picked.app.querySelector("#detail .run-card");
  expect(card.querySelector(".run-card-head b")?.textContent).toBe("Round 1");
  expect(card.querySelector(".run-card-outcome")?.textContent).toBe("Changes requested");
  expect(reviewers(card)).toEqual([["Testing", null], ["Correctness", "approved"]]);
  expect(card.querySelector(".run-card-total")?.textContent).toBe("3 notes");
  expect([...card.querySelectorAll(".run-sev")].map((note) => note.textContent)).toEqual(["1 serious", "2 moderate"]);
  expect([...picked.app.querySelectorAll("#detail .run-pick")].map((pick) => pick.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
  expect(run.querySelector(".run-live.is-waiting")?.textContent).toBe("Waiting on Lead input");
  expect(text).toContain("Now: verify");
  // The run's own id is the product's, not the reader's, so the block does not show it.
  expect(run.querySelector(".run-id")).toBeNull();
  // The timing bar below says how long it took in all, so the run block does not repeat it.
  expect(text).not.toContain("In all");
  // A stop with no run shows the state alone, as before.
  const [, merged] = await open(fixtureDocument(), { steps: [{ click: '.stop-btn[data-subject="PAY-2358"]' }] }, render(fixtureDocument(), { "run-7": summary }));
  expect(merged.app.querySelector("#detail .d1")?.textContent).toBe("Hold the cart through a payment redirect");
  expect(merged.app.querySelector("#detail .d-run")).toBeNull();
  expect(merged.text).not.toContain("Run unavailable");
});

test("takes a live page's runs from the view the server renders after a push", async () => {
  const view = readJson(path.join(SOURCE, "run-view.json"));
  const summary = summarize(view, { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 });
  const later = fixtureDocument();
  const [before, pushed, refreshed] = await open(fixtureDocument(), {
    rendered: render(later, { "run-7": summary }),
    store: { read: "pending" },
    steps: [{ push: later }, { wait: 3300 }],
  });
  expect(before.app.querySelector("#detail .d-run-none")?.textContent).toBe("Run run-7 unavailable.");
  // The push redraws at once, with the runs the page already has...
  expect(pushed.app.querySelector("#detail .d-run")).toBeNull();
  // ...and the runs follow from the re-rendered view.
  expect(refreshed.app.querySelector("#detail .d-run .run-story")?.textContent).toBe("Approved on round 2 · 1 serious note, 2 moderate notes, 1 minor note raised");
});

test("says quietly that a run is unavailable when it could not be read, and still draws the page", async () => {
  for (const runs of [{}, { "run-7": { runId: "run-7", unavailable: true } }, undefined]) {
    const [{ app }] = await open(fixtureDocument(), {}, render(fixtureDocument(), runs));
    expect(app.querySelector("#detail .d-run")).toBeNull();
    expect(app.querySelector("#detail .d-run-none")?.textContent).toBe("Run run-7 unavailable.");
    expect(app.querySelector("#line")?.textContent).toContain("Retry card payments once on a dropped connection");
  }
});

test("never lets run text become markup", async () => {
  const hostileView = {
    spec: "run-view/1",
    sections: [
      { id: "waiting", items: [{ label: HOSTILE, fields: [{ label: HOSTILE, value: HOSTILE }] }] },
      { id: "reviews", items: [{ label: HOSTILE, fields: [{ label: "Round", value: "1" }, { label: "Verdict", value: HOSTILE }] }] },
      { id: "evidence", items: [{ label: "review.round.closed", fields: [{ label: "Reported at", value: HOSTILE },
        { label: "Observation", value: JSON.stringify({ round: 1, outcome: HOSTILE, findings: { P0: 1, P1: 0, P2: 0, P3: 0 } }) }] }] },
      { id: "activity-history", items: [{ label: "x", fields: [{ label: "Phase", value: HOSTILE }, { label: "Last observed", value: "2026-10-22T05:00:00Z" }] }] },
    ],
  };
  const summary = summarize(hostileView, { runId: "run-7", status: HOSTILE });
  const page = render(fixtureDocument(), { "run-7": summary, [HOSTILE]: summary });
  expect(page).not.toContain("</script><img");
  expect(page).not.toContain("\u2028");
  // The record, the runs, the native reviews, the links and the view's configuration, then the program.
  expect(page.match(/<\/script>/gi)).toHaveLength(6);
  const [{ app }] = await open(fixtureDocument(), {}, page);
  const run = app.querySelector("#detail .d-run");
  expect(run.querySelector("img")).toBeNull();
  // The hostile <b>bold</b> never becomes an element; the card's own round heading is the only <b>.
  expect([...run.querySelectorAll("b")].filter((node) => node.textContent === "bold")).toHaveLength(0);
  expect(run.textContent).toContain("</script><img src=x onerror=alert(1)>");
  // An outcome that is not a known verdict is reported in its own words, as text.
  const outcome = run.querySelector(".run-card-outcome");
  expect(outcome.textContent).toBe(`Reported as ${HOSTILE}`);
  expect(outcome.children).toHaveLength(0);
  expect(run.querySelector(".run-bead.is-unknown .run-pick")?.getAttribute("aria-label")).toBe(`Round 1, reported as ${HOSTILE}, 1 critical note`);
});

test("lists each reviewer's findings on its round, and on the confirming round the fixes, each commit linked", async () => {
  const view = reportView();
  const summary = summarize(view, { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 }, readings());
  const page = render(fixtureDocument(), { "run-7": summary });
  const [latest, first] = await open(fixtureDocument(), { steps: [{ click: "#detail .run-pick" }] }, page);
  const findings = (card) => [...card.querySelectorAll(".run-reviewer")].map((group) => [
    group.querySelector(".run-lens-name")?.textContent,
    [...group.querySelectorAll(".run-finding")].map((item) => [item.className, item.querySelector(".run-finding-text")?.textContent,
      item.querySelector(".run-fate")?.textContent ?? null]),
  ]);
  // Round 1 lists each reviewer's findings, worst first, with what became of each, and says it once below.
  const one = first.app.querySelector("#detail .run-card");
  expect(findings(one)).toEqual([
    ["Testing", [["run-finding is-p1", "A retry after a timeout charges twice.", "\u2713 fixed"], ["run-finding is-p2", "No test covers a dropped second attempt.", "open"]]],
    ["Correctness", [["run-finding is-p2", "The receipt keeps the first attempt's total.", "dropped"]]],
  ]);
  // Each severity is said once, as a band over its findings.
  expect([...one.querySelectorAll(".run-band")].map((band) => band.textContent)).toEqual(["Serious", "Moderate", "Moderate"]);
  expect(one.querySelector(".run-settled")?.textContent).toBe("1 of 3 fixed in abc1234 · confirmed round 2 · 1 open");
  // Counts are the findings' own, so the card does not repeat them.
  expect(one.querySelector(".run-card-notes")).toBeNull();
  // Round 2, the confirming round, names the fix it confirmed, linked to its commit.
  const two = latest.app.querySelector("#detail .run-card");
  expect(findings(two)).toEqual([
    ["Testing", []],
    // Every finding on the round is still open, so the line says it once and the finding carries no state.
    ["Correctness", [["run-finding is-p3", "The retry log line repeats the order id.", null]]],
  ]);
  expect(two.querySelector(".run-reviewer .run-quiet")?.textContent).toBe("nothing raised");
  expect(two.querySelector(".run-settled")?.textContent).toBe("Confirmed 1 earlier note fixed in abc1234, 1 earlier note settled otherwise");
  const link = two.querySelector(".run-settled a.run-commit");
  expect(link.getAttribute("href")).toBe(`https://github.com/acme/shop/commit/${FIX}`);
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
});

test("shows a round's counts with a quiet note when its report could not be read", async () => {
  const view = reportView();
  const summary = summarize(view, { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 },
    readings({ "artifact-round-2": null }));
  const [{ app }] = await open(fixtureDocument(), {}, render(fixtureDocument(), { "run-7": summary }));
  const card = app.querySelector("#detail .run-card");
  expect([...card.querySelectorAll(".run-card-notes > *")].map((part) => part.textContent)).toEqual(["1 note", "1 minor", "details unavailable"]);
  expect(card.querySelector(".run-finding")).toBeNull();
});

test("never lets report text become markup, nor a fix commit become a link unless it is a commit id", async () => {
  const round = { round: 1, at: "2026-10-22T04:40:00Z", outcome: "changes", findings: { P0: 1, P1: 0, P2: 0, P3: 0 },
    raised: [{ id: HOSTILE, lens: HOSTILE, severity: HOSTILE, summary: HOSTILE, state: HOSTILE, deferredIssue: null }],
    settled: [{ id: "x", state: "fixed", fixCommit: "javascript:alert(1)", deferredIssue: null }, { id: "y", state: "fixed", fixCommit: FIX, deferredIssue: null }] };
  const summary = { runId: "run-7", ticket: null, open: true, status: null, startedAt: null, lastAt: null, durationSeconds: null, rounds: 1, findings: null, roundDetail: [round] };
  const [{ app }] = await open(fixtureDocument(), {}, render(fixtureDocument(), { "run-7": summary }));
  const card = app.querySelector("#detail .run-card");
  expect(card.querySelector("img")).toBeNull();
  expect([...card.querySelectorAll("b")].filter((node) => node.textContent === "bold")).toHaveLength(0);
  // A summary stored without a full stop gets one.
  expect(card.querySelector(".run-finding-text")?.textContent).toBe(`${HOSTILE}.`);
  expect(card.querySelector(".run-fate")?.textContent).toBe(HOSTILE);
  // The javascript-shaped commit stays text; only the real commit id is linked.
  expect([...card.querySelectorAll("a")].map((link) => link.getAttribute("href"))).toEqual([`https://github.com/acme/shop/commit/${FIX}`]);
  expect([...card.querySelectorAll("code.run-commit")].map((code) => code.textContent)).toEqual(["javascr"]);
});

test("keeps keyboard focus on the round picked, by updating the beads in place", async () => {
  const view = readJson(path.join(SOURCE, "run-view.json"));
  const summary = summarize(view, { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 });
  const [, picked] = await open(fixtureDocument(), { steps: [{ click: "#detail .run-pick" }] }, render(fixtureDocument(), { "run-7": summary }));
  expect(picked.focused).toBe("button");
  expect([...picked.app.querySelectorAll("#detail .run-pick")].map((pick) => pick.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
  expect(picked.app.querySelector("#detail .run-card .run-card-head b")?.textContent).toBe("Round 1");
});

test("keeps the picked round through a live redraw", async () => {
  const view = readJson(path.join(SOURCE, "run-view.json"));
  const summary = summarize(view, { runId: "run-7", status: "incomplete", open: true, durationSeconds: 3000, bytes: 4000 });
  const [, picked, pushed] = await open(fixtureDocument(), { store: { read: "pending" }, steps: [{ click: "#detail .run-pick" }, { push: fixtureDocument() }] },
    render(fixtureDocument(), { "run-7": summary }));
  expect(picked.app.querySelector("#detail .run-card .run-card-head b")?.textContent).toBe("Round 1");
  expect(pushed.app.querySelector("#detail .run-card .run-card-head b")?.textContent).toBe("Round 1");
  expect([...pushed.app.querySelectorAll("#detail .run-pick")].map((pick) => pick.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
});

test("shows the latest round when the picked one is past the run's rounds", async () => {
  const round = (number) => ({ round: number, at: `2026-10-22T04:${10 + number}:00Z`, outcome: "changes", findings: null });
  const summary = (count) => ({ runId: "run-7", ticket: null, open: true, status: null, startedAt: null, lastAt: null, durationSeconds: null,
    rounds: count, findings: null, roundDetail: Array.from({ length: count }, (_, index) => round(index + 1)) });
  const [, picked, pushed, refreshed] = await open(fixtureDocument(), {
    rendered: render(fixtureDocument(), { "run-7": summary(2) }),
    store: { read: "pending" },
    steps: [{ click: "#detail .run-pick", index: 2 }, { push: fixtureDocument() }, { wait: 3300 }],
  }, render(fixtureDocument(), { "run-7": summary(3) }));
  expect(picked.app.querySelector("#detail .run-card .run-card-head b")?.textContent).toBe("Round 3");
  expect(pushed.app.querySelector("#detail .run-card .run-card-head b")?.textContent).toBe("Round 3");
  // The run's summary now has two rounds, so the third cannot be shown: the latest is.
  expect(refreshed.app.querySelectorAll("#detail .run-pick")).toHaveLength(2);
  expect(refreshed.app.querySelector("#detail .run-card .run-card-head b")?.textContent).toBe("Round 2");
  expect([...refreshed.app.querySelectorAll("#detail .run-pick")].map((pick) => pick.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
});

test("takes a round's lens verdicts from the record's review.round entry only when the run gives none", async () => {
  const document = fixtureDocument();
  const entry = (round, verdicts) => ({ at: "2026-10-22T04:59:00Z", id: 90 + round, kind: "review.round", lane: "payments",
    subject: "PAY-2361", summary: `payments review round ${round} on PAY-2361`, review: { round, verdicts } });
  document.journal.push(entry(1, { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "changes" }), entry(2, { "lens.outcome-correctness": "changes" }));
  const summary = { runId: "run-7", ticket: null, open: true, status: null, startedAt: null, lastAt: null, durationSeconds: null, rounds: 2, findings: null,
    roundDetail: [
      { round: 1, at: "2026-10-22T04:40:00Z", outcome: "changes", findings: null },
      { round: 2, at: "2026-10-22T04:58:00Z", outcome: "approve", findings: null, verdicts: { "lens.outcome-correctness": "approve" } },
    ] };
  const [latest, first] = await open(document, { steps: [{ click: "#detail .run-pick" }] }, render(document, { "run-7": summary }));
  // The run's own verdicts stand over the record's.
  expect(reviewers(latest.app.querySelector("#detail"))).toEqual([["Correctness", null]]);
  expect(reviewers(first.app.querySelector("#detail"))).toEqual([["Testing", null], ["Correctness", "approved"]]);
});

test("tells each round's time from the round before it, and colours each badge by its worst note", async () => {
  // The page runs in UTC with Date.now fixed at NOW, so its "yesterday" and "today" are NOW's UTC days.
  const now = new Date(NOW);
  const yesterdayNoon = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - 1, 12, 0));
  const todayLate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 30));
  const start = Date.parse("2026-10-15T10:00:00Z");
  const at = (offsetMinutes) => new Date(start + offsetMinutes * 60000).toISOString();
  const summary = {
    runId: "run-7", ticket: null, open: false, status: null, startedAt: yesterdayNoon.toISOString(), lastAt: todayLate.toISOString(), durationSeconds: null,
    rounds: 9, findings: null,
    roundDetail: [
      { round: 5, at: at(0), outcome: "changes", findings: { P0: 1, P1: 0, P2: 2, P3: 0 } },
      { round: 6, at: at(0.25), outcome: "changes", findings: { P0: 0, P1: 0, P2: 1, P3: 1 } },
      { round: 7, at: at(0.25 + 125), outcome: "changes", findings: { P0: 0, P1: 0, P2: 0, P3: 2 } },
      { round: 8, at: at(0.25 + 125 + 3 * 1440), outcome: "changes", findings: { P0: 0, P1: 0, P2: 0, P3: 0 } },
      { round: 9, at: at(60), outcome: "approve", findings: null,
        verdicts: { "lens.outcome-correctness": "approve", "lens.adversarial-testing": "changes", "lens.security": "blocked" } },
    ],
  };
  const [{ app }] = await open(fixtureDocument(), {}, render(fixtureDocument(), { "run-7": summary }));
  const run = app.querySelector("#detail .d-run");
  // The story counts by the rounds' own numbers, not by how many rounds the summary kept.
  expect(run.querySelector(".run-story")?.textContent).toBe("Approved on round 9 · 1 critical note, 3 moderate notes, 3 minor notes raised");
  const beads = [...run.querySelectorAll(".run-bead")];
  expect(beads.map((bead) => bead.querySelector(".run-badge")?.className ?? null)).toEqual(
    ["run-badge is-p0", "run-badge is-p2", "run-badge is-p3", null, null]);
  const times = beads.map((bead) => bead.querySelector(".run-at")?.textContent ?? "");
  expect(times.slice(1, 4)).toEqual(["moments later", "2 h 5 min later", "3 days later"]);
  // A round that closed before the one before it shows its own time instead of a gap.
  expect(times[4]).not.toMatch(/later$/);
  expect(times[4]).toMatch(/[0-9]+:[0-9]{2} [ap]m$/);
  // A lens verdict that is neither approval nor changes is shown as said.
  expect([...run.querySelectorAll(".run-card .run-lens")].map((lens) => lens.className)).toEqual(
    ["run-lens is-changes", "run-lens is-approve", "run-lens is-unknown"]);
  expect(reviewers(run)).toEqual([["Testing", "asked for changes"], ["Correctness", null], ["Security", "blocked"]]);
  // A run that began yesterday and ended today says both days.
  expect(run.querySelector(".run-start")?.textContent).toBe("yesterday 12:00 pm");
  expect(run.querySelector(".run-end")?.textContent).toBe("today 12:30 am");
});

test("draws whatever the record has, and says so when it has nothing to draw", async () => {
  for (const document of [{ state: {} }, { state: { meta: {}, queue: null, lanes: "x" }, journal: [null, 3] }, { state: { meta: { checking: { now: { steps: [null, {}] } } } } }]) {
    const [{ app }] = await open(document);
    expect(app.querySelector("#now .kicker .headline")?.textContent).toBe("Delivery");
    expect(app.querySelector("#line")?.textContent).toContain("No work on the line yet.");
    expect(app.querySelector("#left .left-number")?.textContent).toBe("0pieces to go");
  }
  expect((await open({}))[0].text).toContain("The delivery record could not be read.");
});

test("follows the live store: its pushes, its read, and its connection", async () => {
  const later = fixtureDocument();
  later.state.meta.headline = "Checkout resilience, later";
  later.state.queue[2].stage = "merged";
  const [before, pushed, down, ignored] = await open(fixtureDocument(), {
    store: { read: "pending" },
    steps: [{ push: later }, { status: false }, { push: { nope: true } }],
  });
  const headline = (snapshot) => snapshot.app.querySelector("#now .kicker .headline")?.textContent;
  expect(headline(before)).toBe("Checkout resilience");
  expect(headline(pushed)).toBe("Checkout resilience, later");
  expect(pushed.app.querySelector("#now h1")?.textContent).toBe("2 of 8 pieces have landed.");
  expect(pushed.source).toBe("live");
  expect(pushed.app.querySelector(".since")?.textContent).toContain("Live");
  expect(down.app.querySelector(".since")?.textContent).toContain("Reconnecting. Showing the last update.");
  expect(headline(down)).toBe("Checkout resilience, later");
  // A pushed answer that is not a record changes nothing.
  expect(headline(ignored)).toBe("Checkout resilience, later");
});

test("marks what changed since the last update, as the earlier map did", async () => {
  const later = fixtureDocument();
  later.state.queue[2].stage = "merged";
  later.state.meta.checking = { now: null, next: [] };
  later.state.lanes[1].state = "handed";
  later.state.followups.push({ id: "f-new", title: "Show the retry count to support", reference: "PAY-2399" });
  later.journal.push({ at: "2026-10-22T05:09:00Z", id: 11, kind: "candidate.merged", lane: "payments", subject: "PAY-2361", summary: "Merged PAY-2361" });
  const [, pushed] = await open(fixtureDocument(), { store: { read: "pending" }, steps: [{ push: later }] });
  const app = pushed.app;
  // The stop that just landed lands, and the line between it and the one before draws itself in.
  expect(app.querySelector('.stop-btn[data-subject="PAY-2361"]')?.className).toContain("landing");
  expect(app.querySelector('.stop-btn[data-subject="PAY-2361"]')?.parentElement?.className).toContain("drawing");
  expect(app.querySelector(".journey .mark")?.className).toContain("moved");
  expect(app.querySelector(".left-number")?.textContent).toBe("6pieces to go");
  expect(app.querySelectorAll(".dots i.closing")).toHaveLength(1);
  expect([...app.querySelectorAll(".person.changed h3")].map((name) => name.textContent)).toEqual(["Storefront"]);
  expect(app.querySelector("#happened .feed li.fresh")?.textContent).toContain("Merged PAY-2361");
  expect([...app.querySelectorAll(".parked li.fresh")].map((item) => item.textContent)).toEqual(["PAY-2399Show the retry count to support"]);
  expect(app.querySelector(".say")?.textContent).toBe("Last landed: Retry card payments once on a dropped connection, 1m ago.");
});

test("shows pieces checked as one batch together, each with its change request", async () => {
  // As the runner writes a batch: every member in checks, one journal start per member, each with its own time.
  const batch = () => {
    const document = fixtureDocument();
    const summary = "Checking Retry card payments once on a dropped connection, Keep the order number on the receipt page as one batch";
    document.state.queue[3].stage = "checking";
    document.state.meta.checking.now.title = "Batch of 2: PAY-2361, PAY-2362";
    document.journal.push(
      { at: "2026-10-22T05:05:00.120431Z", id: 11, kind: "check.started", lane: "payments", subject: "PAY-2361", summary },
      { at: "2026-10-22T05:05:00.187902Z", id: 12, kind: "check.started", lane: "storefront", subject: "PAY-2362", summary },
    );
    return document;
  };
  const [{ app }] = await open(batch());
  const checking = app.querySelector(".checking");
  expect(checking.querySelector(".ck-h")?.textContent).toBe("Being checked together · 2 pieces");
  expect([...checking.querySelectorAll(".ck-members li a")].map((link) => link.getAttribute("href"))).toEqual([
    "https://github.com/acme/shop/pull/1106", "https://github.com/acme/shop/pull/1107"]);
  expect(checking.textContent).toContain("One test run for all of them; each lands only if it matches what was tested");
  expect(app.querySelector("#detail .d-batch")?.textContent).toBe("Checked together with PAY-2362; lands in order once the shared test run passes.");

  // The first member lands: it stays listed as landed while the other is still in checks.
  const landing = batch();
  landing.state.queue[2].stage = "merged";
  landing.state.meta.checking.now.subject = "PAY-2362";
  const [{ app: landingApp }] = await open(landing);
  expect(landingApp.querySelector(".ck-h")?.textContent).toBe("Landing together · 1 of 2 in");
  expect(landingApp.querySelector(".ck-members li.in")?.textContent).toContain("landed");

  // A member sent back to the queue leaves one piece in checks: that is a solo check, not the old batch.
  const split = batch();
  split.state.queue[3].stage = "queued";
  const [{ app: splitApp }] = await open(split);
  expect(splitApp.querySelector(".ck-h")?.textContent).toBe("Being checked now");
  // A failed batch still lands the pieces ahead of the one that broke it; the failures carry every member's logs.
  const failing = batch();
  failing.state.meta.checking.now.steps = [{ label: "shop.check.aa", state: "failed" }, { label: "shop.check.bb", state: "running" }];
  failing.state.queue[3].checks = [{ id: "shop.check.aa", state: "failed", log: "logs/batch-aa.log" }];
  failing.journal.push({ at: "2026-10-22T05:06:00Z", id: 40, kind: "blocker.recorded", lane: null, subject: "PAY-2362", summary: "shop.check.aa fails on the receipt page." });
  const [{ app: failingApp }] = await open(failing);
  expect(failingApp.querySelector(".ck-verdict")?.textContent).toBe(
    "The batch won't land as one. 1 failed · 1 still to run · the pieces ahead of the one that broke it can still land");
  expect(failingApp.querySelector(".ck-fails li")?.textContent).toContain("Log: logs/batch-aa.log");
  expect(failingApp.querySelector(".ck-fails li .ck-note")?.textContent).toBe("▲ shop.check.aa fails on the receipt page.");

  // A member carried out of a failed batch and checked again on its own is not in the old batch any more.
  const carried = batch();
  carried.state.queue[2].stage = "merged";
  carried.state.meta.checking.now = { subject: "PAY-2362", title: "Keep the order number on the receipt page", startedAt: "2026-10-22T05:09:00Z", steps: [] };
  carried.journal.push({ at: "2026-10-22T05:09:00.004211Z", id: 13, kind: "check.started", lane: "storefront", subject: "PAY-2362", summary: "Checking Keep the order number on the receipt page" });
  const [{ app: carriedApp }] = await open(carried);
  expect(carriedApp.querySelector(".ck-h")?.textContent).toBe("Being checked now");
  expect(carriedApp.querySelector("#detail .d-batch")).toBeNull();
  // The same batch started twice in a row counts each member once, and a lane's note between the starts
  // does not split the batch.
  const twice = batch();
  const [first, second] = twice.journal.slice(-2);
  twice.journal.splice(twice.journal.length - 1, 0, { at: "2026-10-22T05:05:00.150000Z", id: 30, kind: "decision.recorded", lane: "ops", subject: null, summary: "A note" });
  twice.journal.push({ ...first, id: 31 }, { ...second, id: 32 });
  const [{ app: twiceApp }] = await open(twice);
  expect(twiceApp.querySelector(".ck-h")?.textContent).toBe("Being checked together · 2 pieces");
  // Pieces in checks that the latest batch start does not cover are not a batch either.
  const [{ app: solo }] = await open(fixtureDocument());
  expect(solo.querySelector(".ck-h")?.textContent).toBe("Being checked now");
});

test("keeps a hand-over the lead has not accepted apart from the work ready to check", async () => {
  const document = fixtureDocument();
  document.state.queue[4].pending = true;
  document.state.meta.checking = { now: null, next: [] };
  const [{ app }] = await open(document);
  expect(app.querySelector('.stop-btn[data-subject="PAY-2363"]')?.className).toBe("stop-btn ready pending");
  const checking = app.querySelector(".checking").textContent;
  expect(checking).not.toContain("ready to be checked");
  expect(checking).toContain("1 more is waiting for the lead to accept the hand-over.");
  expect(app.querySelector('.stop-btn[data-subject="PAY-2363"]')?.getAttribute("aria-label")).toContain("Handed over, waiting for the lead to accept");
});

test("shows a lead's status whole, and a broken main line as needing attention", async () => {
  const document = fixtureDocument();
  document.status = { source: "override", text: "Pausing merges · back after the deploy", expiresAt: "2026-10-22T06:00:00Z" };
  document.journal.push({ at: "2026-10-22T05:08:00Z", id: 12, kind: "base.broken", lane: null, subject: null, summary: "Typecheck fails on main" });
  const [{ app }] = await open(document);
  expect(app.querySelector(".say")?.textContent).toBe("Pausing merges · back after the deploy");
  expect(app.querySelector(".attention")?.textContent).toContain("The main line is brokenTypecheck fails on main");
});

test("keeps a newer push over a late read, and works with a store that cannot subscribe or report status", async () => {
  const pushed = fixtureDocument();
  pushed.state.meta.headline = "Pushed";
  const read = fixtureDocument();
  read.state.meta.headline = "Read late";
  const headline = (snapshot) => snapshot.app.querySelector("#now .kicker .headline")?.textContent;
  const [, afterPush, afterRead] = await open(fixtureDocument(), { store: { read: "manual" }, steps: [{ push: pushed }, { resolve: read }] });
  expect(headline(afterPush)).toBe("Pushed");
  expect(headline(afterRead)).toBe("Pushed");
  const [, noSubscribe] = await open(fixtureDocument(), { store: { read, subscribeThrows: true }, steps: [{ wait: 20 }] });
  expect(headline(noSubscribe)).toBe("Read late");
  const [, noStatus] = await open(fixtureDocument(), { store: { read: "pending", onStatusThrows: true }, steps: [{ push: pushed }] });
  expect(headline(noStatus)).toBe("Pushed");
});

test("keeps the chosen stop, the open history and keyboard focus through an update", async () => {
  const later = fixtureDocument();
  later.state.meta.headline = "Later";
  // Opening the history by its summary moves focus there; focus goes back to the stop before the update.
  const [, chosen, opened, , pushed] = await open(fixtureDocument(), {
    store: { read: "pending" },
    steps: [{ click: '.stop-btn[data-subject="PAY-2358"]' }, { click: "#detail details.mh summary" },
      { click: '.stop-btn[data-subject="PAY-2358"]' }, { push: later }],
  });
  const pressed = (snapshot) => [...snapshot.app.querySelectorAll('.stop-btn[aria-pressed="true"]')].map((stop) => stop.getAttribute("data-subject"));
  expect(pressed(chosen)).toEqual(["PAY-2358"]);
  expect(chosen.app.querySelector("#detail .d1")?.textContent).toBe("Hold the cart through a payment redirect");
  expect(opened.app.querySelector("#detail details.mh")?.hasAttribute("open")).toBe(true);
  expect(pressed(pushed)).toEqual(["PAY-2358"]);
  expect(pushed.app.querySelector("#detail details.mh")?.hasAttribute("open")).toBe(true);
  expect(pushed.focused).toBe("PAY-2358");
});

test("draws again on a status only when the connection changes, so an open card stays open", async () => {
  const hover = { hover: '#line .stop-label [data-ticket="PAY-2361"]' };
  const [, , opened, repeated, dropped] = await open(fixtureDocument(), {
    store: { read: "pending" },
    steps: [{ push: fixtureDocument() }, hover, { status: true }, { status: false }],
  });
  const card = (snapshot) => snapshot.app.querySelector(".issue-card");
  expect(card(opened).hasAttribute("hidden")).toBe(false);
  // The adapter reports "connected" with every pushed record; a status already shown draws nothing.
  expect(card(repeated).hasAttribute("hidden")).toBe(false);
  // A real change of connection draws again, and a redraw closes the card.
  expect(card(dropped).hasAttribute("hidden")).toBe(true);
});

test("keeps a stop chosen by pointing at it through an update", async () => {
  const later = fixtureDocument();
  later.state.meta.headline = "Later";
  const [, pointed, pushed] = await open(fixtureDocument(), { store: { read: "pending" }, steps: [{ hover: '.stop-btn[data-subject="PAY-2358"]' }, { push: later }] });
  expect(pointed.focused).toBeNull();
  expect(pushed.app.querySelector("#detail .d1")?.textContent).toBe("Hold the cart through a payment redirect");
});

test("links only the configured tracker's ids and the first forge's change requests, and opens them apart from the page", async () => {
  const document = fixtureDocument();
  document.state.followups.push({ id: "f-x", title: "Look-alike", reference: "PAY-1x" }, { id: "f-y", title: "Spaced", reference: " PAY-2370" });
  document.state.queue[0].changeRequest = "evil#1";
  document.state.queue[1].changeRequest = "shop #2";
  document.state.queue[3].changeRequest = "shop#1/../x";
  const [{ app }] = await open(document);
  for (const reference of ["PAY-1x", " PAY-2370"]) {
    const node = [...app.querySelectorAll("#parked [data-ticket]")].find((id) => id.getAttribute("data-ticket") === reference);
    expect(node.tagName).toBe("SPAN");
    expect(node.getAttribute("tabindex")).toBe("0");
  }
  const number = (subject) => app.querySelector(`.stop-btn[data-subject="${subject}"]`).parentElement.querySelector(".pr-num");
  expect([number("PAY-2358"), number("PAY-2360"), number("PAY-2362")].map((node) => [node.tagName, node.textContent])).toEqual(
    [["SPAN", "evil#1"], ["SPAN", "shop #2"], ["SPAN", "shop#1/../x"]]);
  const outside = [...app.querySelectorAll("a[href^='https://']")];
  expect(outside.length).toBeGreaterThan(10);
  expect(outside.every((link) => link.getAttribute("target") === "_blank" && link.getAttribute("rel") === "noopener noreferrer")).toBe(true);
});

test("labels same-forge pull request URLs by number with repository-aware accessible names", async () => {
  // One configured origin/template supports arbitrary repositories without per-repository entries.
  const config = { ...CONFIG, forges: [...CONFIG.forges,
    { name: "GitHub", repository: "acme/tools", pullUrl: "https://github.com/{repository}/pull/{number}", commitUrl: "https://github.com/{repository}/commit/{sha}" },
    { name: "Bitbucket", repository: "acme/web", pullUrl: "https://bitbucket.org/{repository}/pull-requests/{number}", commitUrl: "https://bitbucket.org/{repository}/commits/{sha}" }] };
  const document = fixtureDocument();
  document.state.queue[0].changeRequest = "https://github.com/acme/shop/pull/1147";
  document.state.queue[1].changeRequest = "https://github.com/acme/tools/pull/122";
  document.state.queue[2].changeRequest = "https://bitbucket.org/acme/web/pull-requests/42";
  document.state.queue[3].changeRequest = "https://github.com.evil.example/acme/shop/pull/9";
  document.state.queue[4].changeRequest = "https://github.com/acme/elsewhere/pull/7";
  const [{ app }] = await open(document, {}, render(document, {}, { config }));
  const subjects = document.state.queue.slice(0, 5).map((element) => element.subject);
  const number = (subject) => app.querySelector(`.stop-btn[data-subject="${subject}"]`).parentElement.querySelector(".pr-num");
  expect(subjects.map(number).map((node) => [node.tagName, node.textContent, node.getAttribute("href")])).toEqual([
    ["A", "#1147", "https://github.com/acme/shop/pull/1147"],
    ["A", "#122", "https://github.com/acme/tools/pull/122"],
    ["A", "#42", "https://bitbucket.org/acme/web/pull-requests/42"],
    ["SPAN", "https://github.com.evil.example/acme/shop/pull/9", null],
    ["A", "#7", "https://github.com/acme/elsewhere/pull/7"],
  ]);
  for (const [index, repository] of [[0, "acme/shop"], [1, "acme/tools"], [2, "acme/web"], [4, "acme/elsewhere"]]) {
    const link = number(subjects[index]);
    expect(link.hasAttribute("title")).toBe(false);
    expect(link.getAttribute("aria-label")).toBe(`Open pull request ${repository} ${link.textContent}`);
  }
});

test("leaves a pull request URL with a trailing slash, and a URL off the forge's pull path, as text", async () => {
  const document = fixtureDocument();
  document.state.queue[0].changeRequest = "https://github.com/acme/shop/pull/1147/";
  document.state.queue[1].changeRequest = "https://github.com/acme/shop/pull-requests/5";
  const [{ app }] = await open(document);
  const number = (subject) => app.querySelector(`.stop-btn[data-subject="${subject}"]`).parentElement.querySelector(".pr-num");
  expect(["PAY-2358", "PAY-2360"].map(number).map((node) => [node.tagName, node.textContent, node.getAttribute("href")])).toEqual([
    // The configured pull path has no trailing slash, so the URL is not one it matches.
    ["SPAN", "https://github.com/acme/shop/pull/1147/", null],
    ["SPAN", "https://github.com/acme/shop/pull-requests/5", null],
  ]);
});

test("filters the story by tab and shows all of it on request, keeping focus on the tab", async () => {
  const [, everything, landed, problems, back] = await open(fixtureDocument(), {
    steps: [{ click: "#happened .more" }, { click: '#happened [data-tab="landed"]' }, { click: '#happened [data-tab="problems"]' }, { click: '#happened [data-tab="all"]' }],
  });
  const [, decisions] = await open(fixtureDocument(), { steps: [{ click: '#happened [data-tab="decisions"]' }] });
  expect([...decisions.app.querySelectorAll("#happened .feed li .g")].map((mark) => mark.className)).toEqual(["g decide"]);
  const count = (snapshot) => snapshot.app.querySelectorAll("#happened .feed li").length;
  expect(count(everything)).toBe(10);
  // "Show all" removes itself; focus moves to the first entry it revealed.
  expect(everything.focused).toBe("li");
  expect(everything.app.querySelectorAll("#happened .feed li")[8]?.hasAttribute("data-revealed")).toBe(true);
  expect(count(landed)).toBe(1);
  expect(landed.focused).toBe("landed");
  expect(landed.app.querySelector('#happened [data-tab="landed"]')?.getAttribute("aria-pressed")).toBe("true");
  expect(count(problems)).toBe(2);
  expect(count(back)).toBe(8);
  expect(back.focused).toBe("all");
});

test("finds nothing for record values that name built-in properties, and keeps them out of class names", async () => {
  const document = fixtureDocument();
  document.state.lanes[0].id = "constructor";
  document.state.lanes[0].title = "";
  document.state.queue[0].lane = "constructor";
  document.state.lanes[1].state = "toString";
  document.state.lanes[2].state = "x rail";
  document.state.queue[1].stage = "valueOf";
  document.state.queue[2].subject = 2361;
  document.state.meta.checking.now.subject = 2361;
  for (const step of document.state.meta.checking.now.steps) step.state = "chip now";
  const [{ app, text }] = await open(document);
  expect(text).not.toContain("[native code]");
  expect(text).not.toContain("[object Object]");
  expect([...app.querySelectorAll(".chip")].map((chip) => chip.className)).toEqual(["chip handed", "chip", "chip", "chip idle"]);
  expect(new Set([...app.querySelectorAll(".ck-step")].map((step) => step.className))).toEqual(new Set(["ck-step pending"]));
  // An unknown stage is neither ready nor being checked.
  expect(app.querySelector('.stop-btn[data-subject="PAY-2360"]')?.className.trim()).toBe("stop-btn unknown");
  // A number for a subject still names its stop and is still the one being checked.
  expect(app.querySelector('.stop-btn[data-subject="2361"]')?.className.trim()).toBe("stop-btn now");
  expect(app.querySelector('.stop-btn[data-subject="2361"]')?.getAttribute("aria-pressed")).toBe("true");
});

test("reads unreadable times as unknown, never as the start of time", async () => {
  const document = fixtureDocument();
  document.state.meta.updatedAt = "not a time";
  document.state.meta.checking.now.steps[4].startedAt = 12345;
  document.state.meta.checking.now.startedAt = "soon";
  document.state.lanes[3].since = "a while back";
  document.state.leases[0].since = null;
  const [{ app }] = await open(document);
  expect(app.querySelector(".since")?.textContent).toBe("Saved view · base aaaaaaa");
  expect(app.querySelector(".ck-run")?.textContent).toBe("Running storefront build");
  expect(app.querySelector(".leases")?.textContent).toBe("dev-backend held by lead");
  expect(app.querySelectorAll(".person")[3]?.querySelector(".on")).toBeNull();
  expect(app.querySelector(".checking")?.textContent).not.toMatch(/started|[0-9]{3,}d/);
});

test("draws checks as chips up to twelve, says a run is stale after ninety minutes", async () => {
  const steps = (length) => Array.from({ length }, (_, index) => ({ label: `check ${index}`, state: "pending" }));
  const document = fixtureDocument();
  document.state.meta.checking.now.steps = steps(12);
  expect((await open(document))[0].app.querySelectorAll(".ck-step")).toHaveLength(12);
  document.state.meta.checking.now.steps = steps(13);
  expect((await open(document))[0].app.querySelector(".ck-bar")).not.toBeNull();
  document.state.meta.updatedAt = "2026-10-22T03:40:00Z";
  expect((await open(document))[0].app.querySelector(".since")?.className.trim()).toBe("since");
  document.state.meta.updatedAt = "2026-10-22T03:41:00Z";
  expect((await open(document))[0].app.querySelector(".since")?.className.trim()).toBe("since");
  document.state.meta.updatedAt = "2026-10-22T03:39:00Z";
  const stale = (await open(document))[0].app.querySelector(".since");
  expect(stale.className).toBe("since stale");
  expect(stale.textContent).toContain("no update for 1h 31m");
});

test("follows the live server through the adapter it injects into the page", async () => {
  // The page exactly as the server serves it at /view/: the adapter goes in right after the opt-in marker.
  const served = spawnSync("python3", ["-B", "-c", [
    "import sys; sys.path.insert(0, sys.argv[1]); import render_state as r",
    "page = sys.stdin.read()",
    "sys.stdout.write(page.replace(r.LIVE_OPT_IN, r.LIVE_OPT_IN + '\\n<script>\\n' + r._LIVE_ADAPTER + '\\n</script>', 1))",
  ].join("\n"), path.join(ROOT, "skills", "orchestrate-work", "scripts")], { input: render(fixtureDocument()), encoding: "utf8" });
  expect(served.status).toBe(0);
  expect(served.stdout).toContain("new EventSource(\"/events\")");
  const onServer = fixtureDocument();
  onServer.state.meta.headline = "From the server";
  const written = fixtureDocument();
  written.state.meta.headline = "Just written";
  const [, read, pushed, dropped] = await open(fixtureDocument(), { server: { document: onServer }, steps: [{ wait: 20 }, { event: written }, { dropped: true }] }, served.stdout);
  const headline = (snapshot) => snapshot.app.querySelector("#now .kicker .headline")?.textContent;
  expect(headline(read)).toBe("From the server");
  expect(read.app.querySelector(".since")?.textContent).toContain("Connected, waiting for updates");
  expect(headline(pushed)).toBe("Just written");
  expect(pushed.app.querySelector(".since")?.textContent).toContain("Live");
  expect(dropped.app.querySelector(".since")?.textContent).toContain("Reconnecting. Showing the last update.");
  expect(headline(dropped)).toBe("Just written");
});

test("reads the live record once it is open, and says when it cannot", async () => {
  const later = fixtureDocument();
  later.state.meta.headline = "Read from the live record";
  const [, read] = await open(fixtureDocument(), { store: { read: later }, steps: [{ wait: 20 }] });
  expect(read.app.querySelector("#now .kicker .headline")?.textContent).toBe("Read from the live record");
  expect(read.source).toBe("live");
  expect(read.app.querySelector(".since")?.textContent).toContain("Connected, waiting for updates");
  const [, refused] = await open(fixtureDocument(), { store: { read: "reject" }, steps: [{ wait: 20 }] });
  expect(refused.text).toContain("Live record unreadable. Showing the saved view.");
});

test("prints the page for a document on stdin, and nothing for anything else", async () => {
  // A runs command that answers nothing, so the fixture's run-7 is no run this repository recorded.
  const root = repository({ config: CONFIG, runsCommands: {} });
  try {
    const good = renderIn(root, fixtureDocument());
    expect(good.status).toBe(0);
    expect(good.page).toContain('<meta name="delivery-state-live">');
    // The runs command cannot read run-7, so it is unavailable.
    expect(block(good.page, "delivery-runs")).toEqual({ "run-7": { runId: "run-7", unavailable: true } });
    // renderIn sends its document as JSON, so the raw text goes to the same command directly.
    for (const bad of ["not json", "[1]", "null"]) {
      const result = spawnSync("python3", ["-B", RENDER, "line-map", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json"],
        { cwd: root, input: bad, encoding: "utf8" });
      expect(result.status).toBe(1);
      expect(result.stdout).toBe("");
      expect(result.stderr).toContain("line-map:");
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
