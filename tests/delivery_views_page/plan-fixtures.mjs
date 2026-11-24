// Synthetic deliveries for the plan chapter: a record the page draws and the delivery-plan/1 document beside it.
// Run with `node tests/delivery_views_page/plan-fixtures.mjs` to rewrite the golden fixture deliveries, inputs and
// goldens: read every golden it writes before committing it, since a golden pins what the page shows today.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const LENSES = ["lens.outcome-correctness", "lens.adversarial-testing"];
export const RECORD_KINDS = [
  { id: "review", label: "Review", source: "record" },
  { id: "handed", label: "Handed off", source: "record" },
  { id: "merged", label: "Merged", source: "record" },
];
export const APP_KIND = { id: "app", label: "In-app check", source: "producer" };

/** A full commit id for a name, the same every time. */
export function headOf(name, salt = 0) {
  let value = salt + 7;
  for (const character of name) value = (value * 31 + character.charCodeAt(0)) >>> 0;
  return Array.from({ length: 5 }, (_, index) => ((value * (index + 3) + index * 977) >>> 0).toString(16).padStart(8, "0")).join("").slice(0, 40);
}

/** A time some minutes after the fixtures' morning. */
export function minute(count) {
  return new Date(Date.parse("2026-10-30T06:00:00Z") + count * 60000).toISOString().replace(".000Z", "Z");
}

/** A queue item for a subject at a stage, handed off at its head unless another is given. */
export function item(subject, stage, extra = {}) {
  return { order: 0, subject, title: `Work ${subject}`, lane: "lane-1", stage, head: headOf(subject), syncedHead: null,
    leadInputs: [], reportedRounds: null, runId: null, checks: [], ...extra };
}

/** The main line the fixtures' facts name. */
export const BASE = { ref: "main", head: headOf("main") };

/** Each kind's facts when a step gives none, in the shape the registry of kinds gives that kind's data. */
const FACTS = {
  "candidate.handed": (subject) => ({ head: headOf(subject), checks: [] }),
  "candidate.merged": (subject) => ({ head: headOf(subject) }),
  "candidate.stopped": () => ({ reason: "check_failed", detail: "A check failed." }),
  "check.started": () => ({}),
  "check.finished": () => ({}),
  "check.failed": () => ({ check: "suite", timedOut: false, failing: [], more: 0, log: "logs/suite.log" }),
  "check.carried": () => ({ checks: ["lib"], ranAgain: [], base: BASE, commits: [headOf("base-1")] }),
  "base.rerun.started": () => ({ checks: ["suite"], base: BASE }),
  "base.rerun.stopped": (subject) => ({ base: BASE, head: headOf(subject), seconds: 4, why: "The stop exited 143." }),
  "base.broken": () => ({ source: "queue" }),
  "base.repaired": () => ({ source: "lead", head: headOf("main-repaired") }),
  "base.flaky": () => ({ source: "fullHealth", head: BASE.head, failing: ["tests.x.X.test_y"] }),
  "peer.merged": () => ({ peer: "FX-92", head: headOf("peer"), files: ["src/one.txt"] }),
  "handoff.accepted": () => ({ settled: false }),
  "lane.assigned": (subject) => ({ title: `Work ${subject}` }),
  "decision.recorded": () => ({ text: "The lead made a call", why: "It was needed." }),
  "blocker.recorded": () => ({ text: "Work cannot go on" }),
  "delivery.closed": () => ({}),
};
/** The kinds whose entries the registry says name what caused them. */
const CAUSED = new Set(["check.finished", "check.failed", "base.rerun.started", "base.rerun.stopped", "base.rerun.finished",
  "handoff.checked", "handoff.rejected", "release.finished"]);

/**
 * A journal for a sequence of [minute, kind, subject, extra] steps, each entry carrying its kind's facts: `extra.data`
 * over the kind's own, and the rest of `extra` beside them, such as `because`. A kind whose entries name a cause names
 * the entry before it unless told otherwise. `approve` and `changes` stand for review rounds whose required lenses all
 * approve, or one asks for changes.
 */
export function journal(steps) {
  return steps.map(([at, kind, subject, extra = {}], index) => {
    const { data = {}, round, verdicts, ...rest } = extra;
    const entry = { id: index + 1, at: minute(at), kind, lane: "lane-1", subject };
    if (kind === "approve" || kind === "changes" || kind === "review.round") {
      const given = verdicts ?? Object.fromEntries(LENSES.map((lens) => [lens, kind === "changes" ? "changes" : "approve"]));
      return { ...entry, kind: "review.round", ...rest,
        data: { round: round ?? 1, startedAt: minute(at), finishedAt: minute(at), verdicts: given, findings: { P0: 0, P1: 0, P2: 0, P3: 0 }, ...data } };
    }
    if (!FACTS[kind] && !extra.data) throw new Error(`no facts for a ${kind} step: give them as data`);
    const caused = CAUSED.has(kind) && index > 0 ? { because: [index] } : {};
    return { ...entry, ...caused, ...rest, data: { ...(FACTS[kind] ? FACTS[kind](subject) : {}), ...data } };
  });
}

/** The record document a renderer reads. */
export function record({ queue = [], planned = [], journal: entries = [], finish = [], meta = {} } = {}) {
  return {
    state: {
      version: 3, meta: { headline: "Fixture delivery", updatedAt: "2026-10-30T11:59:00Z", ...meta },
      lanes: [{ id: "lane-1", title: "Lane one", focus: "", state: "working", waitingOn: null, since: minute(0) }],
      queue: queue.map((entry, index) => ({ ...entry, order: index + 1 })), planned, finish, followups: [], leases: [],
    },
    journal: entries, stats: {}, estimates: {}, status: { source: "derived", text: "" }, focus: {},
  };
}

/** A unit owing every declared kind unless told otherwise, with one criterion unless given more. */
export function unit(subject, { criteria = 1, proofs = null, title = null, label, removed } = {}) {
  const list = typeof criteria === "number"
    ? Array.from({ length: criteria }, (_, index) => ({ id: `${subject}.${index + 1}`, text: `Criterion ${index + 1} of ${subject}` }))
    : criteria;
  return { subject, ...(label ? { label } : {}), title: title ?? `Work ${subject}`, ...(removed ? { removed: true } : {}), criteria: list,
    proofs: proofs ?? null };
}

/** A delivery-plan/1 document for units; each unit without proofs owes every kind. */
export function planFor(units, { kinds = RECORD_KINDS, finish = [], changes = [], evidence = {}, snapshot = null, approvals, outOfScope,
  updated = "2026-10-30T09:00:00Z", requiredLenses = LENSES, outcome = null, approvedAt = minute(0), review } = {}) {
  const owed = (entry) => ({ ...entry, proofs: entry.proofs ?? kinds.map((kind) => kind.id) });
  const shaped = units.map(owed);
  return {
    schemaVersion: "delivery-plan/1", updated,
    outcome: outcome ?? { summary: "Shoppers can pay again after a dropped connection.", text: "Shoppers can pay again after a dropped connection, and no payment is ever taken twice." },
    requiredLenses, proofKinds: kinds, units: shaped, finish,
    approved: { at: approvedAt, ...(review ? { review } : {}), snapshot: { units: (snapshot ?? units).map(owed), finish } },
    changes, evidence,
    ...(approvals ? { approvals } : {}), ...(outOfScope ? { outOfScope } : {}),
  };
}

/** A finish line in the plan, and the record's own line with the same id. */
export function finishLine(id, covers, { at = minute(0), label = null, text = null } = {}) {
  return { id, label: label ?? `Line ${id}`, text: text ?? `The ${id} line is met`, at, covers };
}

/**
 * The golden delivery with in-app proofs, shaped like a long delivery: 20 units and 29 criteria, 7 changes since
 * approval, a finish line whose explicit list predates 15 added criteria, one open unit a change touched, and a
 * waiver on a proven unit. The chapter shows exactly flags 5 and 6.
 */
export function inAppDelivery() {
  const kinds = [...RECORD_KINDS, APP_KIND];
  const original = [];
  for (let index = 1; index <= 12; index += 1) original.push(unit(`CART-${100 + index}`, { criteria: index <= 2 ? 2 : 1, title: `Cart step ${index}` }));
  const added = [];
  for (let index = 13; index <= 20; index += 1) added.push(unit(`CART-${100 + index}`, { criteria: index <= 19 ? 2 : 1, title: `Added step ${index}` }));
  const units = [...original, ...added];
  const open = "CART-105";
  const steps = [];
  const queue = [];
  let at = 10;
  for (const entry of units) {
    const subject = entry.subject;
    steps.push([at, "approve", subject], [at + 1, "candidate.handed", subject]);
    if (subject !== open) steps.push([at + 2, "candidate.merged", subject]);
    queue.push(item(subject, subject === open ? "queued" : "merged"));
    at += 3;
  }
  const evidence = {};
  for (const entry of units) {
    evidence[entry.subject] = { app: { state: "done", head: headOf(entry.subject), at: minute(200), items: ["checkout-flow"] } };
  }
  evidence["CART-103"].app = { state: "waived", head: headOf("CART-103"), reason: "The device lab was down; the flow is covered by the contract test.", items: ["checkout-flow"] };
  evidence[open].app = { state: "todo", items: ["checkout-flow"] };
  const originalIds = original.flatMap((entry) => entry.criteria.map((criterion) => criterion.id));
  const finish = [
    finishLine("cart-flow", { criteria: originalIds, proofs: ["merged"] }, { label: "Every cart criterion merged", text: "Every cart criterion is merged" }),
    finishLine("verified", { criteria: "all", proofs: ["app"] }, { at: minute(300), label: "Checked in the app", text: "Every criterion is checked in the app" }),
  ];
  const changes = [
    { id: "c1", at: minute(60), kind: "added", subjects: ["CART-113", "CART-114"], by: "lead", why: "The review asked for the retry paths.", title: "Retry paths" },
    { id: "c2", at: minute(90), kind: "added", subjects: ["CART-115", "CART-116"], by: "operator", why: "The operator asked for receipts.", title: "Receipts" },
    { id: "c3", at: minute(120), kind: "added", subjects: ["CART-117", "CART-118"], by: "lane-1", why: "A lane found the refund gap.", title: "Refunds", ref: "#41" },
    { id: "c4", at: minute(150), kind: "added", subjects: ["CART-119", "CART-120"], by: "operator", why: "The operator asked for the audit trail.", title: "Audit trail" },
    { id: "c5", at: minute(180), kind: "relaxed", subjects: ["CART-101"], criteria: ["CART-101.2"], by: "operator", why: "One retry is enough.",
      before: "Criterion 2 of CART-101 retries three times", after: "Criterion 2 of CART-101 retries once" },
    { id: "c6", at: minute(210), kind: "changed", subjects: [open], criteria: [`${open}.1`], by: "lead", why: "The check proved nothing at the old step.",
      before: "Checked after the cart loads", after: "Checked after the payment returns" },
    { id: "c7", at: minute(300), kind: "finish", subjects: [], by: "lead", why: "Every criterion is checked in the app before the release.",
      after: { criteria: "all", proofs: ["app"] } },
  ];
  const plan = planFor(units, { kinds, finish, changes, evidence, snapshot: original, approvedAt: minute(0), review: "Both lenses aligned",
    approvals: [{ text: "Plan approved", state: "done", who: "operator" }, { text: "Release sign-off", state: "waiting", who: "operator" }],
    outOfScope: ["Saved cards", "Gift cards", "Currency conversion"], updated: "2026-10-30T09:00:00Z" });
  plan.approved.snapshot.finish = [finish[0]];
  const document = record({ queue, journal: journal(steps),
    finish: [{ id: "cart-flow", text: "Every cart criterion is merged", met: false }, { id: "verified", text: "Every criterion is checked in the app", met: false }] });
  return { document, plan };
}

/** The golden delivery with record proofs only: three columns, and nothing in the plan needs the engineer. */
export function recordOnlyDelivery() {
  const units = [unit("PAY-1", { criteria: 2, title: "Card retry" }), unit("PAY-2", { title: "Receipt email" }),
    unit("PAY-3", { title: "Refund screen" }), unit("PAY-4", { criteria: 2, title: "Payment audit" })];
  const document = record({
    queue: [item("PAY-1", "merged"), item("PAY-2", "merged"), item("PAY-3", "merged"), item("PAY-4", "queued")],
    journal: journal([[10, "approve", "PAY-1"], [11, "candidate.handed", "PAY-1"], [12, "candidate.merged", "PAY-1"],
      [13, "approve", "PAY-2"], [14, "candidate.handed", "PAY-2"], [15, "candidate.merged", "PAY-2"],
      [16, "approve", "PAY-3"], [17, "candidate.handed", "PAY-3"], [18, "candidate.merged", "PAY-3"],
      [19, "changes", "PAY-4"], [20, "candidate.handed", "PAY-4"]]),
    finish: [{ id: "landed", text: "Every piece lands", met: false }, { id: "proven", text: "Every criterion is proven", met: false }],
  });
  const finish = [finishLine("landed", { criteria: [], proofs: ["merged"] }, { label: "Every piece lands" }),
    finishLine("proven", { criteria: "all", proofs: ["review", "handed", "merged"] }, { label: "Every criterion proven" })];
  return { document, plan: planFor(units, { finish }) };
}

/**
 * A plan review as run-delivery writes it: three rounds to alignment, each fixed finding carrying the commit of the round
 * that reports it closed. `folded` gives twelve rounds instead, rounds 2 to 10 folded into one entry, and round 11
 * listing less than it raised.
 */
export function threeRoundReview({ folded = false } = {}) {
  const [oc, at] = LENSES;
  const counts = (P1 = 0, P2 = 0) => ({ P0: 0, P1, P2, P3: 0 });
  const raised = (id, lens, summary, severity = "P1") => ({ id, lens, severity, summary, state: "resolved" });
  const fixed = (id, lens, commit, resolution) => ({ id, lens, state: "fixed", fixCommit: commit, resolution });
  const first = { round: 1, candidateRef: "1".repeat(40), at: minute(-300), outcome: "changes", verdicts: { [oc]: "changes", [at]: "changes" },
    findings: counts(4), raised: [raised("c1-1", oc, "The fold has no valid shape."), raised("c1-2", oc, "The guarantees conflict."),
      raised("c1-3", oc, "The caps block every plan write."), raised("a1-1", at, "A review past the caps blocks the plan write.")], settled: [] };
  if (!folded) {
    const two = "2".repeat(40);
    return { lenses: [...LENSES], state: "aligned", rounds: [first,
      { round: 2, candidateRef: two, at: minute(-200), outcome: "changes", verdicts: { [oc]: "changes", [at]: "approve" }, findings: counts(1),
        raised: [raised("c2-1", oc, "Scenario 6 reads the wrong rounds.")],
        settled: [fixed("c1-1", oc, two, "C2's numbering rule gives the fold its shape."), fixed("c1-2", oc, two, "The always-kept list settles it."),
          fixed("c1-3", oc, two, "C4a's four steps always fit the caps."), fixed("a1-1", at, two, "The producer fits the review before it writes.")] },
      { round: 3, candidateRef: "3".repeat(40), at: minute(-100), outcome: "approve", verdicts: { [oc]: "approve", [at]: "approve" }, findings: counts(),
        raised: [], settled: [fixed("c2-1", oc, "3".repeat(40), "The off-by-one is corrected: scenario 6 reads rounds 2 to 10.")] }] };
  }
  return { lenses: [...LENSES], state: "aligned", rounds: [first,
    { round: 10, fromRound: 2, candidateRef: "a".repeat(40), at: minute(-150), outcome: "changes", verdicts: { [oc]: "changes", [at]: "approve" },
      findings: counts(9, 2), raised: [], settled: [], clipped: { findings: 11, closures: 7, texts: false } },
    { round: 11, candidateRef: "b".repeat(40), at: minute(-120), outcome: "changes", verdicts: { [oc]: "changes", [at]: "approve" },
      findings: counts(6), raised: [{ ...raised("c11-1", oc, "One finding of six is listed."), state: "resolved" }],
      settled: [], clipped: { findings: 5, closures: 0, texts: true } },
    { round: 12, candidateRef: "c".repeat(40), at: minute(-100), outcome: "approve", verdicts: { [oc]: "approve", [at]: "approve" },
      findings: counts(), raised: [], settled: [fixed("c11-1", oc, "c".repeat(40), "Fixed.")] }] };
}

/**
 * A one-unit delivery whose plan went through three review rounds, with three approvals drafted from the plan, two of
 * them marked blocking, and an out-of-scope list.
 */
export function planReviewDelivery() {
  const document = record({ queue: [item("SHOP-1", "queued")], journal: journal([]) });
  const plan = planFor([unit("SHOP-1", { criteria: 2, title: "Saved addresses" })], { review: "after 3 review rounds · every lens aligned",
    approvals: [{ id: "approval-1", text: "Product Owner signs off the copy", state: "waiting", blocking: true },
      { id: "approval-2", text: "Security reviews the token scope", state: "waiting", blocking: true },
      { id: "approval-3", text: "Release notes are reviewed", state: "waiting" }],
    outOfScope: ["A per-delivery base branch", "Importing addresses"] });
  plan.planReview = threeRoundReview();
  return { document, plan };
}

/**
 * A plan review for a preview, `count` rounds long, ending as `state` says. "open" leaves the last round with one lens
 * approving and the other asking for changes, so the round card names the approving lens's own verdict; "notes-open"
 * gives the last round that outcome; "ended" carries the operator's reason.
 */
export function previewReview(count, state = "aligned") {
  const [oc, at] = LENSES;
  const counts = (P1 = 0) => ({ P0: 0, P1, P2: 0, P3: 0 });
  const rounds = [];
  for (let number = 1; number <= count; number += 1) {
    const last = number === count;
    const commit = String(number).repeat(40);
    const settled = number > 1 ? [{ id: `c${number - 1}-1`, lens: oc, state: "fixed", fixCommit: commit, resolution: `Fixed in round ${number}.` }] : [];
    const verdicts = !last ? { [oc]: "changes", [at]: "approve" }
      : state === "aligned" || state === "notes-open" ? { [oc]: "approve", [at]: "approve" } : { [oc]: "changes", [at]: "approve" };
    const outcome = !last ? "changes" : state === "aligned" ? "approve" : state === "notes-open" ? "notes-open" : "changes";
    const raised = outcome === "approve" ? [] : [{ id: `c${number}-1`, lens: oc, severity: "P1", summary: `Round ${number} found a gap in the plan.`,
      state: last ? "unresolved" : "resolved" }];
    rounds.push({ round: number, candidateRef: commit, at: minute(-60 * (count - number + 1)), outcome, verdicts,
      findings: counts(raised.length), raised, settled });
  }
  return { lenses: [...LENSES], state: state === "notes-open" ? "aligned" : state, ...(state === "ended" ? { endedReason: "The operator ended the review to replan." } : {}),
    rounds };
}

/**
 * A plan preview as its builder writes one: the plan as its own snapshot, no changes or evidence, finish lines only a
 * person checks, the plan's approvals waiting, and the preview member. `rounds` and `state` give its review, none by
 * default; `approval: "handed"` names the delivery that took it; `layout: "other"` has no stories.
 */
export function previewDelivery({ rounds = 0, state = "aligned", approval = "agents-only", delivery = "SHOP-90", layout = "delivery",
  decisions = ["Addresses are kept per account, never per device.", "No new renderer version."] } = {}) {
  const units = layout === "other" ? [] : [unit("SHOP-91", { criteria: 2, title: "Saved addresses" }), unit("SHOP-92", { title: "Address book export" })];
  const finish = [finishLine("finish-1", { criteria: [], proofs: [] }, { label: "Finish 1", text: "Every saved address survives a sign-out" })];
  const plan = planFor(units, { finish, approvedAt: minute(-400), updated: "2026-10-30T11:00:00Z",
    outcome: { summary: "Plan preview (agent review only): Saved addresses", text: "Shoppers keep their addresses between visits." },
    approvals: [{ text: "Product Owner signs off the copy", state: "waiting" }, { text: "The operator approves the plan", state: "waiting" }],
    outOfScope: ["Importing addresses"], requiredLenses: [] });
  plan.preview = { plan: "docs/plans/saved-addresses.md", commit: headOf("plan"), approval, ...(approval === "handed" ? { delivery } : {}), layout,
    decisions };
  if (rounds) plan.planReview = previewReview(rounds, state);
  const document = record({ planned: units.map((entry) => ({ subject: entry.subject, title: entry.title, lane: "lane-1", stage: "planned" })),
    finish: finish.map((line) => ({ id: line.id, text: line.text, met: false })), meta: { headline: plan.outcome.summary } });
  return { document, plan };
}

/** The preview fixtures: no round, three rounds open, aligned, ended, handed, a plan outside the delivery layout, and none decided. */
export const PREVIEW_CASES = {
  "preview-none": () => previewDelivery(),
  "preview-open": () => previewDelivery({ rounds: 3, state: "open" }),
  "preview-aligned": () => previewDelivery({ rounds: 3 }),
  "preview-aligned-first": () => previewDelivery({ rounds: 1 }),
  "preview-notes-open": () => previewDelivery({ rounds: 2, state: "notes-open" }),
  "preview-ended": () => previewDelivery({ rounds: 2, state: "ended" }),
  "preview-handed": () => previewDelivery({ rounds: 3, approval: "handed" }),
  "preview-other": () => previewDelivery({ rounds: 2, layout: "other" }),
  "preview-no-decisions": () => previewDelivery({ rounds: 1, decisions: [] }),
};

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PLAN_CASES = { "plan-in-app": inAppDelivery, "plan-record-only": recordOnlyDelivery, "plan-review": planReviewDelivery };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const corpus = path.join(HERE, "..", "fixtures", "delivery-views", "line-map");
  for (const [name, build] of Object.entries(PLAN_CASES)) {
    const { document, plan } = build();
    const folder = path.join(corpus, name);
    mkdirSync(folder, { recursive: true });
    writeFileSync(path.join(folder, "document.json"), JSON.stringify(document, null, 2) + "\n");
    writeFileSync(path.join(folder, "plan.json"), JSON.stringify(plan, null, 2) + "\n");
    writeFileSync(path.join(folder, "runs-commands.json"), "{}\n");
    const { block, openPage, readJson, renderPage } = await import("./support.mjs");
    process.env.TZ = "UTC";
    const now = "2026-10-30T12:00:00Z";
    const page = renderPage(document, { config: readJson(path.join(corpus, "linked-config.json")), runsCommands: {}, plan });
    const [first] = await openPage(page, { now });
    const golden = { now, blocks: { "delivery-state": block(page, "delivery-state"), "delivery-runs": block(page, "delivery-runs"),
      "delivery-plan": block(page, "delivery-plan") }, initialText: first.text, folds: {}, details: {} };
    for (const fold of first.app.querySelectorAll(".plan-fold")) {
      const name = fold.getAttribute("data-fold");
      const [, opened] = await openPage(page, { now, steps: [{ toggle: `.plan-fold[data-fold="${name}"]` }] });
      golden.folds[name] = opened.app.querySelector(`.plan-fold[data-fold="${name}"]`).textContent;
    }
    for (const subject of [plan.units[0].subject, plan.units.find((entry) => document.state.queue.some((queued) => queued.subject === entry.subject && queued.stage !== "merged")).subject]) {
      const [, shown] = await openPage(page, { now, steps: [{ click: `:is(.stop-btn, .mg-tick)[data-subject="${subject}"]` }] });
      golden.details[subject] = { detail: shown.app.querySelector("#detail").textContent };
    }
    writeFileSync(path.join(folder, "golden.json"), JSON.stringify(golden, null, 2) + "\n");
  }
}
