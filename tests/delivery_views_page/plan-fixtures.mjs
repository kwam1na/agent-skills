// Synthetic deliveries for the plan chapter: a record the page draws and the delivery-plan/1 document beside it.
// Run with `node tests/delivery_views_page/plan-fixtures.mjs` to rewrite the two golden fixture deliveries, inputs and
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

/**
 * A journal for a sequence of [minute, kind, subject, extra] steps; `approve` and `changes` stand for review rounds
 * whose required lenses all approve, or one asks for changes.
 */
export function journal(steps) {
  return steps.map(([at, kind, subject, extra = {}], index) => {
    const entry = { id: index + 1, at: minute(at), kind, lane: "lane-1", subject, summary: `${kind} ${subject ?? ""}`.trim() };
    if (kind === "approve" || kind === "changes" || kind === "review.round") {
      const verdicts = extra.verdicts ?? Object.fromEntries(LENSES.map((lens) => [lens, kind === "changes" ? "changes" : "approve"]));
      return { ...entry, kind: "review.round", summary: `${subject}: review round ${extra.round ?? 1}`,
        review: { round: extra.round ?? 1, startedAt: minute(at), finishedAt: minute(at), verdicts, findings: { P0: 0, P1: 0, P2: 0, P3: 0 } } };
    }
    return { ...entry, ...extra };
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

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PLAN_CASES = { "plan-in-app": inAppDelivery, "plan-record-only": recordOnlyDelivery };

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
      const [, shown] = await openPage(page, { now, steps: [{ click: `.stop-btn[data-subject="${subject}"]` }] });
      golden.details[subject] = { detail: shown.app.querySelector("#detail").textContent };
    }
    writeFileSync(path.join(folder, "golden.json"), JSON.stringify(golden, null, 2) + "\n");
  }
}
