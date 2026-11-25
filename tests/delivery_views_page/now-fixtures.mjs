// The Now page's fixture deliveries: one record for each of the twelve stages a delivery passes through and for each of
// the three overrides, with the Needs you list and the forge's facts beside it, and what Now must say of it. Run with
// `node tests/delivery_views_page/now-fixtures.mjs` to rewrite tests/fixtures/delivery-views/now/: read every expectation
// it writes before committing it, since the page tests and the hosted projection test both read those files.
import { mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { headOf, item, journal, minute, record } from "./plan-fixtures.mjs";

export const NOW_FIXTURES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "fixtures", "delivery-views", "now");
const NOW = minute(360);
const KEY = "APP-10";
const REQUEST = "https://github.com/acme/app/pull/7";
export const CONFIG = { spec: "delivery-views-config/1", forges: [{ name: "GitHub", repository: "acme/app",
  pullUrl: "https://github.com/{repository}/pull/{number}", commitUrl: "https://github.com/{repository}/commit/{sha}" }] };
const INTEGRATION = { base: "integrate/app", target: "main", remote: "origin" };
const H = headOf("integration"), H2 = headOf("integration-2");
// Nine stories in three lanes, in landing order.
const LANES = { A1: "lane-1", A2: "lane-1", A3: "lane-1", A4: "lane-1", B1: "lane-2", B2: "lane-2", B3: "lane-2", C1: "lane-3", C2: "lane-3" };
const STORIES = Object.keys(LANES);

const landed = (subject, at) => [[at, "approve", subject], [at + 1, "candidate.handed", subject], [at + 2, "candidate.merged", subject]];
const everyLanding = STORIES.flatMap((subject, index) => landed(subject, 10 + index * 3));
const suiteStarted = (at, head, extra = {}) => [at, "integration.suite.started", KEY, { data: { head, startedAt: minute(at), ...extra } }];
const suite = (at, head, result = "passed", extra = {}) => [at, "integration.suite", KEY, { data: { head, result, seconds: 1500, ...extra } }];
const reviewStarted = (at, head, round) => [at, "review.started", KEY, { data: { round, head, lenses: ["lens.outcome-correctness"], restart: false } }];
const round = (at, head, number, verdict, findings = {}) => [at, "review.round", KEY, { round: number,
  verdicts: { "lens.outcome-correctness": verdict, "lens.adversarial-testing": "approve" },
  data: { head, findings: { P0: 0, P1: 0, P2: 0, P3: 0, ...findings } } }];
const merged = (at, head) => [at, "integration.merged", KEY, { data: { target: "main", targetHead: headOf("main-" + at), head, conflicts: [] } }];
const requested = (at, head) => [at, "integration.requested", KEY, { data: { changeRequest: REQUEST, head } }];
const rejoined = (at) => [at, "integration.landed", KEY, { data: { target: "main", head: headOf("main-final") } }];
// Everything before the change request, passing: the suite and an approving review at the integration's head.
const throughReview = [suite(50, H), reviewStarted(55, H, 1), round(60, H, 1, "approve")];

/** A record of the nine stories, every one landed unless `queue` says otherwise, then `steps` under the delivery's key. */
function delivery(steps = [], { queue = null, planned = [], integration = INTEGRATION, landings = everyLanding, meta = {}, followups } = {}) {
  const entries = journal([...landings, ...steps]);
  // The integration facts are the lead's, under the delivery's own key, with no lane.
  entries.forEach((entry) => { if (entry.subject === KEY) { entry.lane = null; } else { entry.lane = LANES[entry.subject] ?? entry.lane; } });
  const document = record({ queue: queue ?? STORIES.map((subject) => item(subject, "merged", { lane: LANES[subject] })), planned, journal: entries,
    meta: { trackerParent: KEY, baseHead: headOf("base"), updatedAt: minute(359), ...(integration ? { integration } : {}), ...meta } });
  if (followups) { document.state.followups = followups; }
  return document;
}
/** The supervisor's list as it writes it: running and current unless told otherwise. */
function needs(items = [], over = {}) {
  return { spec: "delivery-needs-you/1", running: true, pass: 3, at: minute(359), staleAfter: minute(362),
    summary: items.length ? `${items.length} thing needs you.` : "Nothing needs anyone, and nothing is holding up the finish.",
    words: { heading: "Waiting on someone", holding: "Holding up the finish", age: "for {age}" }, items, ...over };
}
const trackerLag = { holding: true, source: "tracker publisher", text: "The tracker publisher hasn't posted journal entries 70", clears: "c",
  kind: "trackerLag", who: "lead", said: { headline: "The tracker is behind the record", why: "The tracker publisher hasn't posted journal entries 70.",
    clears: "Clears when the publisher posts them on a later pass.", who: "The lead" } };
const operatorCall = { holding: true, source: "lane-2's state", text: "lane-2 asks", clears: "c", kind: "laneAsks", who: "operator", lane: "lane-2",
  said: { headline: "Lane 2 waits on you about keeping a story that grew past its plan", why: "Keep it whole, or split it?",
    clears: "Clears when Lane 2 no longer waits on you.", who: "You" } };
const SUITE = { label: "Suite", state: "done" }, REVIEW = { label: "Review", state: "done" }, MERGE = { label: "Merge", state: "done" };
const todo = (label) => ({ label, state: "todo" });
const live = (label, who = null) => ({ label, state: "live", ...(who ? { who } : {}) });
const stop = (label, who = null) => ({ label, state: "stop", ...(who ? { who } : {}) });
const marks = (...given) => given.map(({ label, state, who }) => [label, state, who ?? null]);
// Six stories in: lane 3's both, two of lane 1's four and two of lane 2's three; A3 is in review and B3 is in the queue.
const sixIn = STORIES.map((subject) => ["A3", "A4", "B3"].includes(subject) ? null : item(subject, "merged", { lane: LANES[subject] })).filter(Boolean);
const sixLandings = ["A1", "A2", "B1", "B2", "C1", "C2"].flatMap((subject, index) => landed(subject, 10 + index * 3));

/** The fixtures: each a record and the blocks beside it, the moment the page is read at, and what Now says. */
export function fixtures() {
  const stages = [
    ["01-stories-landing", "Stories still landing", delivery([], { landings: sixLandings,
      queue: [...sixIn, item("B3", "queued", { lane: "lane-2" })],
      planned: [{ subject: "A3", title: "Work A3", lane: "lane-1", stage: "review" }, { subject: "A4", title: "Work A4", lane: "lane-1", stage: "planned" }] }),
    { sentence: "6 of 9 stories are in. A3 is in review.", marks: marks({ label: "Lane 3 · 2 of 2", state: "done" }, live("Lane 1 · 2 of 4"), live("Lane 2 · 2 of 3"), todo("Rejoin")) }],
    ["02-last-landed", "Last story landed", delivery([]),
      { sentence: "All 9 stories are in. The full suite runs next.", marks: marks(live("Suite", "lead"), todo("Review"), todo("Merge"), todo("Landed")) }],
    ["03-suite-running", "Suite running", delivery([suiteStarted(332, H, { expectedSeconds: 31 * 60 })]),
      { sentence: "All 9 stories are in. The full suite is running: 28 min, about 31 usual.",
        marks: marks(live("Suite · 28 min", "lead"), todo("Review"), todo("Merge"), todo("Landed")) }],
    ["04-suite-failed", "Suite failed", delivery([suite(50, H, "failed")]),
      { sentence: `The full suite failed at ${H.slice(0, 7)}. The lead is fixing it.`, fact: "What happened has the run.", factStop: true,
        marks: marks(stop("Suite", "lead"), todo("Review"), todo("Merge"), todo("Landed")) }],
    ["05-over-budget", "Suite over budget: your call", delivery([suite(50, H, "over-budget", { seconds: 1800, budgetSeconds: 1740 })]),
      { sentence: "Every test passed, 1 min over budget. It's your call to accept.", fact: "30 min against a budget of 29 min.",
        marks: marks(live("Suite", "you"), todo("Review"), todo("Merge"), todo("Landed")) }],
    ["06-review-running", "Review running", delivery([suite(50, H), reviewStarted(55, H, 1), round(60, H, 1, "changes", { P1: 1 }), reviewStarted(70, H, 2)]),
      { sentence: "The integration review is in round 2.", marks: marks(SUITE, live("Review · round 2", "lead"), todo("Merge"), todo("Landed")) }],
    ["07-review-fix", "Review asked for a fix", delivery([suite(50, H), reviewStarted(55, H, 1), round(60, H, 1, "changes", { P1: 1 })]),
      { sentence: "Round 1 asked for 1 fix. The lead is making it.", marks: marks(SUITE, live("Review · fixing", "lead"), todo("Merge"), todo("Landed")) }],
    ["08-waits-for-merge", "Waits for your merge", delivery([...throughReview, requested(70, H)]),
      { sentence: "All 9 stories are in. #7 waits for your merge.", link: ["#7", REQUEST], fact: "1 forge check fails, on main too.",
        marks: marks(SUITE, REVIEW, live("Merge", "you"), todo("Landed")),
        integration: { checks: { pending: 0, failing: [{ name: "lint", onTarget: true }] } } }],
    ["09-checks-running", "Checks still running", delivery([...throughReview, requested(70, H)]),
      { sentence: "All 9 stories are in. #7's checks are still running.", link: ["#7", REQUEST],
        marks: marks(SUITE, REVIEW, live("Merge", "forge"), todo("Landed")), integration: { checks: { pending: 2, failing: [] } } }],
    ["10-target-moved", "Target moved", delivery([...throughReview, requested(70, H), merged(80, H2)]),
      { sentence: "main moved. The lead is bringing #7 up to date.", link: ["#7", REQUEST],
        marks: marks(SUITE, REVIEW, live("Merge · updating", "lead"), todo("Landed")) }],
    ["11-closing", "Merged, closing", delivery([...throughReview, requested(70, H), rejoined(90)],
      { followups: [{ id: "A1-F1", reference: "A1-F1" }, { id: "B2-F3", reference: "B2-F3" }, { id: "B3-F1", reference: "V26-9" }] }),
      { sentence: "#7 is in main. The delivery is closing.", link: ["#7", REQUEST], fact: "Filing 2 follow-ups.",
        marks: marks(SUITE, REVIEW, MERGE, live("Landed · closing")) }],
    ["12-closed", "Closed", delivery([...throughReview, requested(70, H), rejoined(90), [100, "delivery.closed", KEY]]),
      { sentence: "Delivered Oct 30: 9 stories into main.", marks: marks(SUITE, REVIEW, MERGE, { label: "Landed", state: "done" }) }],
    ["13-needs-your-call", "Override: the lead needs your call", delivery([suite(50, H), reviewStarted(55, H, 1), round(60, H, 1, "changes", { P1: 1 }), reviewStarted(70, H, 2)]),
      { sentence: "Lane 2 waits on you about keeping a story that grew past its plan.", fact: "Review waits on you.",
        marks: marks(SUITE, live("Review · round 2", "you"), todo("Merge"), todo("Landed")), needs: needs([operatorCall, trackerLag]) }],
    ["14-work-stopped", "Override: work stopped", delivery([[300, "base.broken", null, { data: { source: "queue" } }]], { landings: sixLandings,
      queue: [...sixIn, item("B3", "queued", { lane: "lane-2", repair: { namedAt: minute(301) } })],
      planned: [{ subject: "A3", title: "Work A3", lane: "lane-1", stage: "review" }, { subject: "A4", title: "Work A4", lane: "lane-1", stage: "planned" }] }),
    { sentence: "The queue has stopped: the main line is broken. B3 is the repair.", stop: true, fact: "Nothing merges until the repair lands.", factStop: true,
      marks: marks(stop("Lane 2 · repairing"), { label: "Lane 3 · 2 of 2", state: "done" }, live("Lane 1 · 2 of 4"), todo("Rejoin")) }],
    ["15-out-of-date", "Fact: the page may be out of date", delivery([...throughReview, requested(70, H)]),
      { sentence: "All 9 stories are in. #7 waits for your merge.", link: ["#7", REQUEST],
        fact: "The lead's supervisor stopped 12 minutes ago, so this page may be out of date.", factStop: true,
        marks: marks(SUITE, REVIEW, live("Merge", "you"), todo("Landed")),
        needs: needs([], { running: false, at: minute(348), staleAfter: undefined }) }],
    // A tracker behind the record, with nothing else holding: Now names it nowhere.
    ["16-tracker-lag-only", "A tracker lag alone", delivery([...throughReview, requested(70, H)]),
      { sentence: "All 9 stories are in. #7 waits for your merge.", link: ["#7", REQUEST],
        marks: marks(SUITE, REVIEW, live("Merge", "you"), todo("Landed")), needs: needs([trackerLag]) }],
  ];
  return stages.map(([name, title, document, expected]) => {
    const { needs: list = null, integration = null, ...now } = expected;
    if (list && list.staleAfter === undefined) { delete list.staleAfter; }
    return { name, title, now: NOW, document, needs: list, integration,
      expected: { sentence: now.sentence, link: now.link ?? null, stop: now.stop ?? false, marks: now.marks, fact: now.fact ?? null, factStop: now.factStop ?? false } };
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  rmSync(NOW_FIXTURES, { recursive: true, force: true });
  mkdirSync(NOW_FIXTURES, { recursive: true });
  for (const fixture of fixtures()) {
    writeFileSync(path.join(NOW_FIXTURES, `${fixture.name}.json`), JSON.stringify(fixture, null, 2) + "\n");
  }
  console.log(`wrote ${readdirSync(NOW_FIXTURES).length} fixtures to ${path.relative(process.cwd(), NOW_FIXTURES)}`);
}
