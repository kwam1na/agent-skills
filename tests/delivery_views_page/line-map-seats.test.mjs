// A shared record's page says whose each call is. The seat that owns a call reads "you"; every other seat reads the
// owner's label; a call whose seat can't be told reads by role, and only the reader's own call takes over Now. The
// rejoin's person words are the delivery holder's. A page with no seat, the hosted one, names every owner by label.
// Each case is drawn from one two-seat record at the rejoin's "waits for the merge" stage: Ama (a) holds the delivery,
// Ben (b) holds the queue and lane 1, and each seat's page reads its own Needs you list.
process.env.TZ = "UTC";

import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { expect, openPage, pageWithRuns } from "./support.mjs";
import { CONFIG, NOW_FIXTURES } from "./now-fixtures.mjs";

/** What Now shows: its sentence, its marks as [label, state, holder] and its fact. */
function nowOf(app) {
  const box = app.querySelector("#now .now-state");
  const fact = box.querySelector(".now-fact");
  return { sentence: box.querySelector(".now-sentence").textContent, fact: fact ? fact.textContent : null,
    marks: [...box.querySelectorAll(".now-mark")].map((mark) => [mark.querySelector(".now-mark-label").textContent,
      mark.getAttribute("data-state"), mark.getAttribute("data-who")]) };
}

const fixture = (name) => JSON.parse(readFileSync(path.join(NOW_FIXTURES, name + ".json"), "utf8"));
const MERGE = fixture("08-waits-for-merge");
const NOW = MERGE.now;
const A = "s-aaaaaaaaaaaa", B = "s-bbbbbbbbbbbb";

/** The merge stage's record, shared by Ama and Ben, with each scope where the journal puts it: `moves` are further
 * transfers, each [scope, from, to], made by the scope's holder after the first ones. */
function shared(moves = [], base = MERGE.document, seats = {}) {
  const document = structuredClone(base);
  document.state.meta.collab = { key: "APP-10", peers: {}, seats: {
    [A]: { label: "Ama", admitted: true, approver: true }, [B]: { label: "Ben", admitted: true, approver: true }, ...seats } };
  let id = Math.max(...document.journal.map((entry) => entry.id));
  const seq = { [A]: 0, [B]: 0 };
  const entry = (kind, data, seat) => ({ id: ++id, at: "2026-10-30T11:00:00Z", kind, lane: null, subject: null, data,
    origin: { seat, seq: ++seq[seat], seen: {} } });
  const transfer = ([scope, from, to]) => entry("scope.transferred", { scope, from, to }, from);
  document.journal.push(entry("record.started", {}, A), ...[["queue", A, B], ["lane:lane-1", A, B], ...moves].map(transfer));
  return document;
}
/** A list item as a pass writes it on a shared record: its owner stamp and its three wordings. */
function item(kind, who, { scope, seat, said, all = said, role, lane } = {}) {
  const words = (pair) => (pair ? { who: pair[0], headline: pair[1] } : undefined);
  return { holding: false, source: "s", text: kind, clears: "c", kind, who, ...(lane ? { lane } : {}), ...(scope ? { scope } : {}),
    ...(seat ? { seat } : {}), said: words(said), saidAll: words(all), saidRole: words(role) };
}
function needs(items, summary = "the pass's own sentence", summaryAll = "the sentence naming every owner") {
  return { spec: "delivery-needs-you/1", running: true, pass: 3, at: "2026-10-30T11:59:30Z", staleAfter: "2026-10-30T12:01:30Z",
    summary, summaryAll, items };
}
/** Ben's draft, waiting to be marked ready: the queue's, so Ben's. */
const DRAFT = { scope: "queue", seat: B, role: ["Someone", "Someone marks the draft ready"] };
const draftFor = { [A]: item("drafts", "operator", { ...DRAFT, said: ["Ben", "Ben marks the draft ready"] }),
  [B]: item("drafts", "operator", { ...DRAFT, said: ["You", "Mark the draft ready"], all: ["Ben", "Ben marks the draft ready"] }) };
/** Lane 2 asks its lead, which is Ama's. */
const ASKS = { scope: "lane:lane-2", seat: A, lane: "lane-2", all: ["Ama's lead", "Lane 2 waits on Ama's lead"], role: ["The lead", "Lane 2 waits on a lead"] };
const asksFor = { [A]: item("laneAsks", "lead", { ...ASKS, said: ["The lead", "Lane 2 waits on the lead"] }),
  [B]: item("laneAsks", "lead", { ...ASKS, said: ["Ama's lead", "Lane 2 waits on Ama's lead"] }) };

/** The page as the renderer embeds it: the list, the forge's facts and, on a local page, the seat it is drawn for. */
function page(document, list, viewer, integration = MERGE.integration) {
  const blocks = [["delivery-needs", list], ["delivery-integration", integration], ["delivery-viewer", viewer ? { seat: viewer } : null]]
    .filter(([, value]) => value)
    .map(([id, value]) => `<script type="application/json" id="${id}">${JSON.stringify(value)}</script>\n`).join("");
  return pageWithRuns(document, {}, { config: CONFIG }).replace("<script>\n", blocks + "<script>\n");
}
/** What the reader sees of whose each call is: Now, the rows in order, the summary, the seat line and the rejoin's line. */
async function drawn(document, list, viewer, at = MERGE) {
  const [{ app }] = await openPage(page(document, list, viewer, at.integration), { now: at.now, store: false });
  const rows = [...app.querySelectorAll(".needs-you .needs-row")];
  return {
    app, now: nowOf(app),
    rows: rows.map((row) => [row.querySelector(".nr-who").textContent, row.querySelector(".a1").textContent,
      row.hasAttribute("data-yours") ? "yours" : row.classList.contains("is-theirs") ? "theirs" : ""]),
    summary: app.querySelector(".needs-you .needs-summary")?.textContent ?? null,
    seat: [...app.querySelectorAll("#now .seat-line > span")].map((node) => [node.className, node.textContent]),
    // The rejoin's own line on the main line's last step: who merges the change request.
    ledger: app.querySelector('#line [aria-label^="Change request: "]')?.getAttribute("aria-label") ?? null,
    leading: app.querySelector(".needs-you")?.classList.contains("is-leading") ?? false,
  };
}
const merge = (who, name) => ["Merge" + (name ? " · " + name : ""), "live", who];

test("drawn as each seat, each reads its own call as theirs, the other's under its label, and the rejoin is Ama's", async () => {
  const lists = { [A]: needs([draftFor[A], asksFor[A]], "1 thing needs Ben, and 1 thing needs the lead."),
    [B]: needs([draftFor[B], asksFor[B]], "1 thing needs you, and 1 thing needs Ama's lead.") };
  const asA = await drawn(shared(), lists[A], A);
  // Ama holds the delivery: the merge is hers. Ben's draft is his, under his label, quieter, and takes nothing over.
  expect(asA.now.sentence).toBe("All 9 stories are in. #7 waits for your merge.");
  expect(asA.now.marks[2]).toEqual(merge("you", "you"));
  expect(asA.rows).toEqual([["Ben", "Ben marks the draft ready", "theirs"], ["The lead", "Lane 2 waits on the lead", ""]]);
  expect([asA.summary, asA.leading, asA.ledger?.endsWith("The merge is yours.")]).toEqual(["1 thing needs Ben, and 1 thing needs the lead.", false, true]);
  expect(asA.seat).toEqual([["seat-chip", "Ama · holds delivery"], ["seat-shared", "Shared with Ben"]]);
  // Ben's own call leads his page and takes Now; the lane's call to Ama's lead takes nothing over.
  const asB = await drawn(shared(), lists[B], B);
  expect(asB.now.sentence).toBe("Mark the draft ready.");
  expect(asB.now.marks[2]).toEqual(merge("you", "you"));
  expect(asB.rows).toEqual([["You", "Mark the draft ready", "yours"], ["Ama's lead", "Lane 2 waits on Ama's lead", ""]]);
  expect([asB.summary, asB.leading, asB.ledger?.endsWith("The merge is Ama's.")]).toEqual(["1 thing needs you, and 1 thing needs Ama's lead.", true, true]);
  expect(asB.seat).toEqual([["seat-chip", "Ben · holds queue · lane 1"], ["seat-shared", "Shared with Ama"]]);
  // With no call of his own, Ben's page says the merge waits on Ama, and marks it hers.
  const quiet = await drawn(shared(), needs([asksFor[B]]), B);
  expect(quiet.now.sentence).toBe("All 9 stories are in. #7 waits for Ama's merge.");
  expect(quiet.now.marks[2]).toEqual(merge("seat", "Ama"));
});

test("a lead's call stamped with the reader's seat is never the reader's: it takes nothing over", async () => {
  const asA = await drawn(shared(), needs([asksFor[A]]), A);
  expect(asA.now.sentence).toBe("All 9 stories are in. #7 waits for your merge.");
  expect(asA.now.fact).toBe(MERGE.expected.fact);
  expect([asA.rows, asA.leading]).toEqual([[["The lead", "Lane 2 waits on the lead", ""]], false]);
});

test("with a seat, a person's call the pass couldn't stamp with a seat reads by role, takes nothing over and ranks with the rest", async () => {
  const unresolved = item("drafts", "operator", { scope: "queue", said: ["You", "Mark the draft ready"], all: ["Ben", "Ben marks it"],
    role: ["Someone", "Someone marks the draft ready"] });
  const asA = await drawn(shared(), needs([asksFor[A], unresolved]), A);
  expect(asA.rows).toEqual([["The lead", "Lane 2 waits on the lead", ""], ["Someone", "Someone marks the draft ready", ""]]);
  expect([asA.now.sentence, asA.now.marks[2], asA.leading]).toEqual(["All 9 stories are in. #7 waits for your merge.", merge("you", "you"), false]);
});

test("a stamp the journal has outrun reads by role on every page, and takes nothing over", async () => {
  // The queue went back from Ben to Ama after Ben's pass stamped the draft his.
  const moved = shared([["queue", B, A]]);
  for (const [viewer, list] of [[B, needs([draftFor[B]])], [null, needs([draftFor[A]])], [A, needs([draftFor[A]])]]) {
    const shown = await drawn(moved, list, viewer);
    expect(shown.rows).toEqual([["Someone", "Someone marks the draft ready", ""]]);
    expect([shown.now.sentence.endsWith("merge."), shown.leading]).toEqual([true, false]);
  }
});

test("a blocker journaled on Ben, with no lane, is Ben's on his page and under his label on Ama's, and takes nothing over", async () => {
  const blocker = { scope: "seat:" + B, seat: B, role: ["A lead", "APP-5's blocker waits on a lead"] };
  // Ben is not the record's first seat, so his seat must hold his own blocker by its stamp, not by the first seat.
  const asB = await drawn(shared(), needs([item("blocker", "lead", { ...blocker, said: ["The lead", "APP-5's blocker waits on the lead"],
    all: ["Ben's lead", "APP-5's blocker waits on Ben's lead"] })]), B);
  expect([asB.rows, asB.leading]).toEqual([[["The lead", "APP-5's blocker waits on the lead", ""]], false]);
  expect(asB.now.sentence).toBe("All 9 stories are in. #7 waits for Ama's merge.");
  const asA = await drawn(shared(), needs([item("blocker", "lead", { ...blocker, said: ["Ben's lead", "APP-5's blocker waits on Ben's lead"] })]), A);
  expect(asA.rows).toEqual([["Ben's lead", "APP-5's blocker waits on Ben's lead", ""]]);
  expect(asA.now.sentence).toBe("All 9 stories are in. #7 waits for your merge.");
});

test("once the delivery moves to Ben, feedback on a section nobody holds is Ben's, and so is the merge", async () => {
  const moved = shared([["delivery", A, B]]);
  const feedback = { scope: "plan:goal", seat: B, role: ["Someone", "Someone settles the feedback on Goal"] };
  const asB = await drawn(moved, needs([item("feedback", "operator", { ...feedback, said: ["You", "Settle the feedback on Goal"],
    all: ["Ben", "Ben settles the feedback on Goal"] })]), B);
  expect([asB.now.sentence, asB.rows[0][2], asB.leading]).toEqual(["Settle the feedback on Goal.", "yours", true]);
  expect(asB.seat[0]).toEqual(["seat-chip", "Ben · holds delivery · queue · lane 1"]);
  const asA = await drawn(moved, needs([item("feedback", "operator", { ...feedback, said: ["Ben", "Ben settles the feedback on Goal"] })]), A);
  expect(asA.rows).toEqual([["Ben", "Ben settles the feedback on Goal", "theirs"]]);
  expect([asA.now.sentence, asA.now.marks[2]]).toEqual(["All 9 stories are in. #7 waits for Ben's merge.", merge("seat", "Ben")]);
});

test("with no seat, the hosted page names every owner and says you nowhere in Needs you, Now or the rejoin", async () => {
  // The delivery holder's list feeds the hosted page: Ama's own call is said to her as "you", and to everyone as "Ama".
  const own = item("feedback", "operator", { scope: "plan:goal", seat: A, said: ["You", "Settle the feedback on Goal"],
    all: ["Ama", "Ama settles the feedback on Goal"], role: ["Someone", "Someone settles the feedback on Goal"] });
  const hosted = await drawn(shared(), needs([own, draftFor[A], asksFor[A]], "1 thing needs you, 1 needs Ben and 1 the lead.",
    "1 thing needs Ama, 1 needs Ben and 1 Ama's lead."), null);
  expect(hosted.rows).toEqual([["Ama", "Ama settles the feedback on Goal", "theirs"], ["Ben", "Ben marks the draft ready", "theirs"],
    ["Ama's lead", "Lane 2 waits on Ama's lead", ""]]);
  expect(hosted.summary).toBe("1 thing needs Ama, 1 needs Ben and 1 Ama's lead.");
  expect([hosted.now.sentence, hosted.now.marks[2], hosted.leading]).toEqual(["All 9 stories are in. #7 waits for Ama's merge.", merge("seat", "Ama"), false]);
  expect(hosted.ledger.endsWith("The merge is Ama's.")).toBe(true);
  expect(hosted.seat).toEqual([["seat-shared", "Shared by Ama and Ben"]]);
  const rejoin = [...hosted.app.querySelectorAll("#line [aria-label], #line .mg-step-said")].map((node) => node.getAttribute("aria-label") || node.textContent);
  for (const words of [hosted.app.querySelector(".needs-you").textContent, hosted.app.querySelector("#now .now-state").textContent, ...rejoin]) {
    expect(/\byou(r|rs)?\b/i.test(words)).toBe(false);
  }
});

test("a record nobody shares reads every person's call as the reader's, whatever seat the page was drawn for", async () => {
  const call = fixture("13-needs-your-call");
  for (const viewer of [null, A]) {
    const blocks = [["delivery-needs", call.needs], ["delivery-integration", call.integration], ["delivery-viewer", viewer ? { seat: viewer } : null]]
      .filter(([, value]) => value)
      .map(([id, value]) => `<script type="application/json" id="${id}">${JSON.stringify(value)}</script>\n`).join("");
    const [{ app }] = await openPage(pageWithRuns(call.document, {}, { config: CONFIG }).replace("<script>\n", blocks + "<script>\n"),
      { now: call.now, store: false });
    const shown = nowOf(app);
    expect([shown.sentence, shown.marks[1], shown.fact]).toEqual([call.expected.sentence,
      [call.expected.marks[1][0] + " · you", "live", "you"], call.expected.fact]);
    expect(app.querySelector("#now .seat-line")).toBeNull();
  }
});

test("the call to accept an over-budget suite is the delivery holder's: hers on her page, under her label on Ben's", async () => {
  const over = fixture("05-over-budget");
  const record = shared([], over.document);
  const asA = await drawn(record, needs([asksFor[A]]), A, over);
  expect([asA.now.sentence, asA.now.marks[0]]).toEqual([over.expected.sentence, [over.expected.marks[0][0] + " · you", "live", "you"]]);
  expect(asA.now.sentence.endsWith("It's your call to accept.")).toBe(true);
  const asB = await drawn(record, needs([asksFor[B]]), B, over);
  expect([asB.now.sentence, asB.now.marks[0]]).toEqual([over.expected.sentence.replace("It's your call", "It's Ama's call"),
    [over.expected.marks[0][0] + " · Ama", "live", "seat"]]);
});

test("another seat's call names that seat on the mark it waits at, and takes nothing over", async () => {
  // While stories land, a lane's mark names no holder: Ben's call about lane 2 names him on it, on Ama's page.
  const landing = fixture("01-stories-landing");
  const call = item("unaccepted", "operator", { scope: "queue", seat: B, lane: "lane-2", said: ["Ben", "Ben accepts lane 2's hand-off"],
    role: ["Someone", "Someone accepts lane 2's hand-off"] });
  const asA = await drawn(shared([], landing.document), needs([call]), A, landing);
  expect(asA.now.sentence).toBe(landing.expected.sentence);
  const lane = landing.expected.marks.findIndex(([label]) => label.startsWith("Lane 2"));
  expect(asA.now.marks[lane]).toEqual([landing.expected.marks[lane][0] + " · Ben", landing.expected.marks[lane][1], "seat"]);
  expect(asA.now.marks.filter((made) => made[2] === "seat")).toHaveLength(1);
});

test("the reader's own calls lead; another seat's rank with the rest, in the list's order", async () => {
  const own = item("feedback", "operator", { scope: "plan:goal", seat: A, said: ["You", "Settle the feedback on Goal"],
    all: ["Ama", "Ama settles it"], role: ["Someone", "Someone settles it"] });
  const asA = await drawn(shared(), needs([asksFor[A], draftFor[A], own]), A);
  expect(asA.rows.map((row) => [row[0], row[2]])).toEqual([["You", "yours"], ["The lead", ""], ["Ben", "theirs"]]);
});

test("a stamped call with no scope stays its seat's: the journal can't outrun a stamp it can't check", async () => {
  const call = item("drafts", "operator", { seat: B, said: ["You", "Mark the draft ready"], all: ["Ben", "Ben marks it"],
    role: ["Someone", "Someone marks it"] });
  const asB = await drawn(shared(), needs([call]), B);
  expect([asB.now.sentence, asB.rows[0][2]]).toEqual(["Mark the draft ready.", "yours"]);
});

test("the seat line names only admitted seats, and a page for a seat the record doesn't have is drawn for nobody", async () => {
  const record = shared([], MERGE.document, { "s-cccccccccccc": { label: "Cy", admitted: false, approver: false } });
  expect((await drawn(record, needs([]), A)).seat).toEqual([["seat-chip", "Ama · holds delivery"], ["seat-shared", "Shared with Ben"]]);
  const stranger = await drawn(record, needs([draftFor[B]]), "s-eeeeeeeeeeee");
  expect(stranger.seat).toEqual([["seat-shared", "Shared by Ama and Ben"]]);
  // It reads as the hosted page does: every owner by label.
  expect(stranger.rows).toEqual([["Ben", "Ben marks the draft ready", "theirs"]]);
  expect(stranger.summary).toBe("the sentence naming every owner");
});
