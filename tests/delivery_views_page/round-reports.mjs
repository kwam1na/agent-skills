// A run whose two review rounds each captured a round report, for the line map's report tests: the run view
// (the shared fixture's rounds plus its report references and `finding.observed` history) and the product's
// `runs artifact --json` reading of each report.
import { createHash } from "node:crypto";
import path from "node:path";
import { CORPUS, readJson } from "./support.mjs";

export const RUN_ID = "run-7";
export const FIX = "abc1234".padEnd(40, "0");
const TREES = { 1: "1".repeat(40), 2: "2".repeat(40) };

const BASE = readJson(path.join(CORPUS, "source", "run-view.json"));

export function report(round, overrides = {}) {
  const raised = round === 1
    ? [
      { id: "r1-f1", lens: "lens.adversarial-testing", severity: "P1", summary: "A retry after a timeout charges twice", state: "open", deferredIssue: null },
      { id: "r1-f2", lens: "lens.outcome-correctness", severity: "P2", summary: "The receipt keeps the first attempt's total", state: "open", deferredIssue: null },
      { id: "r1-f3", lens: "lens.adversarial-testing", severity: "P2", summary: "No test covers a dropped second attempt", state: "open", deferredIssue: null },
    ]
    : [{ id: "r2-f1", lens: "lens.outcome-correctness", severity: "P3", summary: "The retry log line repeats the order id", state: "open", deferredIssue: null }];
  const resolved = round === 1 ? [] : [
    { id: "r1-f1", state: "fixed", fixCommit: FIX, deferredIssue: null },
    { id: "r1-f2", state: "dropped", fixCommit: null, deferredIssue: null },
  ];
  return { spec: "review-round-report/1", runId: RUN_ID, ticket: "PAY-2361", round, roundId: `round-${round}`,
    candidateTreeSha: TREES[round], closedAt: "2026-10-22T04:40:00Z", raised, resolved, ...overrides };
}

/** The product's reading of a report, as `runs artifact <run> <id> --json` prints it; `metadata` overrides the capture's own. */
export function reading(body, metadata = {}) {
  const bytes = Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
  const round = typeof body === "object" && body !== null ? body.round : null;
  return { spec: "run-artifact/1", runId: RUN_ID, ok: true, base64: bytes.toString("base64"),
    metadata: { artifactId: `artifact-round-${round}`, digest: createHash("sha256").update(bytes).digest("hex"), sizeBytes: bytes.length,
      round, roundId: `round-${round}`, candidateTreeSha: TREES[round] ?? null, ...metadata } };
}

/** Each report's reading by artifact id, as a plain object. */
export function readings(entries = {}) {
  return { "artifact-round-1": reading(report(1)), "artifact-round-2": reading(report(2)), ...entries };
}

/** A report reference as the run view lists it; an unavailable one names no attachment. */
export function reference(round, artifactId) {
  return { id: `report-round-${round}`, label: "review", ...(artifactId ? { artifactId } : {}),
    fields: [{ label: "Report", value: `report-round-${round}` }, { label: "Availability", value: artifactId ? "referenced" : "unavailable" }] };
}

function observed(findingId, state) {
  return { id: findingId, label: findingId, fields: [{ label: "State", value: state }] };
}

/** The shared run view with report references (by default one a round) and its findings' observed states, in run order. */
export function reportView(references = [reference(1, "artifact-round-1"), reference(2, "artifact-round-2")]) {
  const history = [observed("r1-f1", "unresolved"), observed("r1-f2", "unresolved"), observed("r1-f3", "unresolved"),
    observed("r2-f1", "unresolved"), observed("r1-f1", "resolved"), observed("r1-f2", "resolved")];
  return { ...BASE, sections: [...BASE.sections, { id: "reports", items: references }, { id: "finding-history", items: history }] };
}
