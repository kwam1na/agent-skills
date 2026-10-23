// Captures golden outputs of the parked history page (4ec1b64) for a fixture corpus.
// Each case is a stdin document, the recorded `runs` command outputs and the
// export files; the page collects from a replay of them at a fixed `asOf`, and
// opens in jsdom at a fixed clock in UTC.
//
//   bun .characterize/capture-history.ts <out> delivery-records-epic=<document.json>
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { JSDOM } from "jsdom";
import { collectHistory, type HistorySources, renderHistoryPage } from "../scripts/delivery-history-view";

const ROOT = path.resolve(import.meta.dir, "..");
const OPEN_PAGE = path.join(ROOT, "scripts/fixtures/delivery-live-view/open-page.cjs");
const EXPORTS = path.join(ROOT, "telemetry/delivery-runs");
const [outRoot, ...records] = process.argv.slice(2);

type Recorded = Record<string, string | null>;
type Case = { id: string; asOf: string; document: unknown; recorded: () => Promise<Recorded>; exports: Record<string, string> };

// Real records and run listings name this machine's home directory; the corpus carries none.
const scrub = (text: string) => text.replaceAll(`${process.env.HOME}/`, "~/");
const key = (args: string[]) => args.join(" ");

/** The history's sources, answered from a recording: a null or missing command fails. */
function replay(recorded: Recorded, exports: Record<string, string>): HistorySources {
  const answer = async (args: string[]) => {
    const stdout = recorded[key(args)];
    if (stdout === undefined || stdout === null) throw new Error(`${key(args)} failed`);
    return JSON.parse(stdout) as unknown;
  };
  return {
    listRuns: () => answer(["runs", "list", "--json"]),
    showRun: (runId) => answer(["runs", "show", runId, "--json"]),
    exports: async () => Object.entries(exports).map(([file, text]) => ({ file, text })),
  };
}

/** The live product, through the checkout's own command, scrubbed. */
function live(args: string[]): string | null {
  const result = spawnSync("bun", ["scripts/delivery-product.ts", ...args], { cwd: ROOT, encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  return result.status === 0 ? scrub(result.stdout) : null;
}

// The fixtures of the source's own history tests.
const HOSTILE = "</script><img src=x onerror=alert(1)> <b>bold</b> next line & \"quoted\" <!--<script>";
function runExport(runId: string, ticket: string, startedAt: string, lastAt: string, open = false) {
  return {
    spec: "delivery-run-export/2",
    runId,
    events: [
      { kind: "run.started", at: startedAt },
      { kind: "review.round.closed", at: lastAt, payload: { round: 1, outcome: "aligned", findings: { P0: 0, P1: 1, P2: 0, P3: 2 } } },
    ],
    summary: { ticket, open, startedAt, lastAt, durationSeconds: 5400, roundsClosed: 1, findings: { P0: 0, P1: 1, P2: 0, P3: 2 } },
    readout: { status: open ? "incomplete" : "complete" },
  };
}
const LEGACY = {
  schemaVersion: 1,
  generatedAt: "2026-08-13T17:38:52.076Z",
  branch: "codex/v26-1212-review-scope",
  status: "pass",
  reviewLoop: { iterationCount: 5, findingCounts: { P0: 0, P1: 1, P2: 2, P3: 3 } },
};
const listed = (...runIds: string[]) =>
  JSON.stringify({ spec: "run-inventory/1", runs: runIds.map((runId) => ({ runId, status: "complete", open: false, durationSeconds: 60, bytes: 10 })) });
const STATE = {
  state: {
    meta: { headline: "Delivery records split (V26-2829)", trackerParent: "V26-2829" },
    queue: [{ subject: "V26-2822", lane: "lane-recorder", stage: "merged", runId: "run-a" }],
    planned: [{ subject: "V26-2825", lane: "lane-map", stage: "planned", runId: "run-c" }],
  },
  journal: [],
};
const OVERLAP_SHOWS: Recorded = {
  "runs list --json": listed("run-a", "run-b"),
  "runs show run-a --json": JSON.stringify(runExport("run-a", "V26-2822", "2026-10-20T09:00:00Z", "2026-10-20T10:30:00Z")),
  "runs show run-b --json": JSON.stringify(runExport("run-b", "V26-2823", "2026-10-21T09:00:00Z", "2026-10-21T10:30:00Z", true)),
};
const OVERLAP_EXPORTS: Record<string, string> = {
  "run-b.json": JSON.stringify(runExport("run-b", "V26-STALE", "2026-10-21T09:00:00Z", "2026-10-21T09:10:00Z")),
  "run-c.json": JSON.stringify(runExport("run-c", "V26-2825", "2026-10-19T09:00:00Z", "2026-10-19T10:00:00Z")),
  "2026-08-13T17-38-52-076Z-codex-v26-1212-review-scope.json": JSON.stringify(LEGACY),
  "delivery-record--0c21.json": JSON.stringify({ gateId: "gate", claims: [] }),
};
const NOW = "2026-10-22T12:00:00Z";
const hostileRun = runExport("run-h", HOSTILE, "2026-10-21T09:00:00Z", "2026-10-21T10:00:00Z");
(hostileRun.events[1]!.payload as { outcome: string }).outcome = HOSTILE;

const cases: Case[] = [
  { id: "overlap", asOf: NOW, document: STATE, recorded: async () => OVERLAP_SHOWS, exports: OVERLAP_EXPORTS },
  { id: "empty", asOf: NOW, document: { state: {}, journal: [] }, recorded: async () => ({ "runs list --json": listed() }), exports: {} },
  { id: "run-list-unreadable", asOf: NOW, document: STATE, recorded: async () => ({ "runs list --json": null }), exports: OVERLAP_EXPORTS },
  {
    id: "unreadable-rows", asOf: NOW, document: STATE,
    recorded: async () => ({
      "runs list --json": JSON.stringify({ spec: "run-inventory/1", runs: [
        { runId: "run-a", status: "complete", open: false, durationSeconds: 60, bytes: 10 },
        { runId: "run-x", status: "incomplete", open: true, durationSeconds: 7260, bytes: 10 },
      ] }),
      "runs show run-a --json": OVERLAP_SHOWS["runs show run-a --json"]!,
      "runs show run-x --json": null,
    }),
    exports: { "run-broken.json": "{ not json", "notes.json": JSON.stringify({ hello: "world" }) },
  },
  {
    id: "hostile-text", asOf: NOW,
    document: { state: { meta: { headline: HOSTILE }, queue: [{ subject: HOSTILE, runId: "run-h" }] } },
    recorded: async () => ({ "runs list --json": listed("run-h"), "runs show run-h --json": JSON.stringify(hostileRun) }),
    exports: { "x.json": JSON.stringify({ ...LEGACY, branch: `codex/v26-9-${HOSTILE}` }) },
  },
];

// A real delivery record, its recorded runs read live, and a slice of the tracked exports.
const REAL_EXPORTS = [
  "run-38d692406f9a48d3.json",
  "run-75790b0ebc44cfe0.json",
  "2026-08-29T01-28-25-020Z-codex-v26-1429-portable-kernel-canary.json",
  "2026-09-02T00-27-43-827Z-claude-realign-mvp-finish-line.json",
  "delivery-record--496b66c0ce73bc0f626574afa439e623aa9122fad133557e13620e02874255de.json",
];
for (const record of records) {
  const [id, recordPath] = record.split("=") as [string, string];
  const document = JSON.parse(scrub(readFileSync(recordPath, "utf8"))) as { state: { meta: { updatedAt: string }; queue?: Array<{ runId?: string }>; planned?: Array<{ runId?: string }> } };
  const runIds = [...(document.state.queue ?? []), ...(document.state.planned ?? [])].flatMap((element) => (element.runId ? [element.runId] : []));
  cases.push({
    id, asOf: document.state.meta.updatedAt, document,
    recorded: async () => {
      const inventory = JSON.parse(live(["runs", "list", "--json"]) ?? "{}") as { spec?: string; runs?: Array<{ runId: string }> };
      const recorded: Recorded = { "runs list --json": JSON.stringify({ ...inventory, runs: (inventory.runs ?? []).filter((row) => runIds.includes(row.runId)) }) };
      for (const runId of runIds) recorded[key(["runs", "show", runId, "--json"])] = live(["runs", "show", runId, "--json"]);
      return recorded;
    },
    exports: Object.fromEntries(REAL_EXPORTS.map((file) => [file, scrub(readFileSync(path.join(EXPORTS, file), "utf8"))])),
  });
}

/** Opens the page in the source's own jsdom harness; `served` gives it the live server's address. */
function openPage(page: string, now: string, steps: unknown[], served = false) {
  const folder = mkdtempSync(path.join(tmpdir(), "golden-history-"));
  try {
    writeFileSync(path.join(folder, "page.html"), page);
    writeFileSync(path.join(folder, "scenario.json"), JSON.stringify({ now, steps, ...(served ? { rendered: page } : {}) }));
    const result = spawnSync("node", [OPEN_PAGE, path.join(folder, "page.html"), path.join(folder, "scenario.json")],
      { cwd: ROOT, encoding: "utf8", env: { ...process.env, TZ: "UTC" }, maxBuffer: 256 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(result.stderr);
    return (JSON.parse(result.stdout) as Array<{ html: string }>).map(({ html }) => new JSDOM(`<main>${html}</main>`).window.document.querySelector("main")!);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
}
const block = (page: string, id: string) => JSON.parse(new RegExp(`<script type="application/json" id="${id}">([^<]*)</script>`).exec(page)![1]!);
const lineMapHref = (app: Element) => [...app.querySelectorAll(".kicker a")].map((link) => link.getAttribute("href"));

for (const item of cases) {
  const dir = path.join(outRoot!, item.id);
  mkdirSync(path.join(dir, "exports"), { recursive: true });
  const recorded = await item.recorded();
  const history = await collectHistory(item.document, replay(recorded, item.exports), item.asOf);
  const page = renderHistoryPage(history);
  const [first] = openPage(page, item.asOf, []);
  const [served] = openPage(page, item.asOf, [], true);
  const keys = [...first!.querySelectorAll("[data-run]")].map((row) => row.getAttribute("data-run")!);
  const details: Record<string, { detail: string; closed: boolean }> = {};
  keys.forEach((rowKey, index) => {
    const button = `[data-run] > button`;
    const [, opened, closed] = openPage(page, item.asOf, [{ click: button, index }, { click: button, index }]);
    const row = opened!.querySelectorAll("[data-run]")[index]!;
    const detail = row.nextElementSibling!;
    details[rowKey] = {
      detail: detail.textContent ?? "",
      closed: (closed!.querySelectorAll("[data-run]")[index]!.nextElementSibling as Element).hasAttribute("hidden"),
    };
  });
  writeFileSync(path.join(dir, "document.json"), `${JSON.stringify(item.document, null, 2)}\n`);
  writeFileSync(path.join(dir, "runs-commands.json"), `${JSON.stringify(recorded, null, 2)}\n`);
  for (const [file, text] of Object.entries(item.exports)) writeFileSync(path.join(dir, "exports", file), text);
  writeFileSync(path.join(dir, "golden.json"), `${JSON.stringify({
    asOf: item.asOf, now: item.asOf, timeZone: "UTC",
    blocks: { "delivery-history": block(page, "delivery-history") },
    initialText: first!.textContent,
    lineMapHref: { file: lineMapHref(first!), served: lineMapHref(served!) },
    rows: keys,
    details,
  }, null, 2)}\n`);
  console.log(item.id, keys.length, "rows", history.notes.length, "notes");
}
