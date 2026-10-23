// Captures golden outputs of the parked line map (4ec1b64) for a fixture corpus.
// Each case is a stdin document plus the recorded `runs` command outputs; the
// renderer runs against a product that replays them, at a fixed clock in UTC.
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { JSDOM } from "jsdom";
import { renderPage } from "../scripts/delivery-live-view";
import { collectRunSummaries, runProduct, type Product } from "../scripts/delivery-line-map-runs";
import { readings, reportView } from "../scripts/fixtures/delivery-live-view/round-reports";

const ROOT = path.resolve(import.meta.dir, "..");
const FIXTURE = path.join(ROOT, "scripts", "fixtures", "delivery-live-view");
// Each record argument is <case id>=<document path>.
const [outRoot, ...records] = process.argv.slice(2);

function fixtureDocument(): Record<string, unknown> {
  const state = JSON.parse(readFileSync(path.join(FIXTURE, "state.json"), "utf8"));
  const journal = readFileSync(path.join(FIXTURE, "journal.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  return {
    state, journal,
    status: { source: "derived", text: "Checking Retry card payments once on a dropped connection, 3 of 6 checks done · last landed V26-2358 at 2026-10-22T04:26:00Z · blocked: backend (waiting on the dev backend lease) · stopped: V26-2360 · next: V26-2363" },
    focus: {},
    stats: {
      "V26-2358": { rounds: 2, roundsSource: "reported", waitSeconds: 120, checkToMergeSeconds: 840, totalSeconds: 4860, outcome: "merged" },
      "V26-2361": { rounds: 3, roundsSource: "reported", waitSeconds: 60 },
    },
    estimates: { "V26-2363": { position: 1, expectedStart: "2026-10-22T05:19:22Z" } },
  };
}

type Recorded = Record<string, string | null>;
// Real records and run listings name this machine's home directory; the corpus carries none.
const scrub = (text: string) => text.replaceAll(`${process.env.HOME}/`, "~/");
const key = (args: string[]) => args.join(" ");
const replay = (recorded: Recorded): Product => async (args) => {
  const stdout = recorded[key(args)];
  return stdout === undefined || stdout === null ? { ok: false, stdout: "" } : { ok: true, stdout };
};
const recording = (recorded: Recorded): Product => async (args) => {
  const result = await runProduct(args);
  recorded[key(args)] = result.ok ? scrub(result.stdout) : null;
  return result;
};

const LISTED = { runId: "run-7", status: "incomplete", open: true, current: false, durationSeconds: 3000, bytes: 4000 };
const list = (rows: unknown[]) => JSON.stringify({ spec: "run-inventory/1", runs: rows });
const runView = JSON.parse(readFileSync(path.join(FIXTURE, "run-view.json"), "utf8"));

async function liveRecorded(document: unknown): Promise<Recorded> {
  const recorded: Recorded = {};
  const cache = path.join(mkdtempSync(path.join(tmpdir(), "golden-cache-")), "cache.json");
  await collectRunSummaries(document, { product: recording(recorded), cachePath: cache });
  return recorded;
}

const cases: Array<{ id: string; now: string; document: unknown; recorded: () => Promise<Recorded> }> = [
  { id: "parked-no-runs", now: "2026-10-22T05:10:00Z", document: fixtureDocument(), recorded: async () => ({ "runs list --json": list([]) }) },
  { id: "parked-run-view", now: "2026-10-22T05:10:00Z", document: fixtureDocument(),
    recorded: async () => ({ "runs list --json": list([LISTED]), "runs view run-7 --json": JSON.stringify(runView) }) },
  { id: "parked-round-reports", now: "2026-10-22T05:10:00Z", document: fixtureDocument(),
    recorded: async () => {
      const view = reportView();
      const read = readings();
      return { "runs list --json": list([LISTED]), "runs view run-7 --json": JSON.stringify(view),
        ...Object.fromEntries([...read].map(([id, value]) => [`runs artifact run-7 ${id} --json`, JSON.stringify(value)])) };
    } },
  { id: "parked-round-report-unreadable", now: "2026-10-22T05:10:00Z", document: fixtureDocument(),
    recorded: async () => {
      const view = reportView();
      return { "runs list --json": list([LISTED]), "runs view run-7 --json": JSON.stringify(view),
        "runs artifact run-7 artifact-round-1 --json": JSON.stringify(readings().get("artifact-round-1")),
        "runs artifact run-7 artifact-round-2 --json": null };
    } },
];
for (const record of records) {
  const [id, recordPath] = record.split("=") as [string, string];
  const document = JSON.parse(scrub(readFileSync(recordPath, "utf8")));
  const state = (document as { state: { meta: { updatedAt: string } } }).state;
  cases.push({ id, now: state.meta.updatedAt, document, recorded: () => liveRecorded(document) });
}

function openPage(page: string, now: string, steps: unknown[]) {
  const folder = mkdtempSync(path.join(tmpdir(), "golden-open-"));
  try {
    writeFileSync(path.join(folder, "page.html"), page);
    writeFileSync(path.join(folder, "scenario.json"), JSON.stringify({ now, store: { read: "pending" }, steps }));
    const result = spawnSync("node", [path.join(FIXTURE, "open-page.cjs"), path.join(folder, "page.html"), path.join(folder, "scenario.json")],
      { cwd: ROOT, encoding: "utf8", env: { ...process.env, TZ: "UTC" }, maxBuffer: 256 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(result.stderr);
    return (JSON.parse(result.stdout) as Array<{ html: string }>).map(({ html }) => new JSDOM(`<main>${html}</main>`).window.document.querySelector("main")!);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
}
const block = (page: string, id: string) => JSON.parse(new RegExp(`<script type="application/json" id="${id}">([^<]*)</script>`).exec(page)![1]!);

for (const item of cases) {
  const dir = path.join(outRoot, item.id);
  mkdirSync(dir, { recursive: true });
  const recorded = await item.recorded();
  const cache = path.join(mkdtempSync(path.join(tmpdir(), "golden-cache-")), "cache.json");
  const runs = await collectRunSummaries(item.document, { product: replay(recorded), cachePath: cache });
  const page = renderPage(item.document, runs);
  const [first] = openPage(page, item.now, []);
  const subjects = [...first!.querySelectorAll(".stop-btn")].map((stop) => stop.getAttribute("data-subject")!);
  const details: Record<string, { detail: string; rounds?: string[] }> = {};
  for (const subject of subjects) {
    const stop = `.stop-btn[data-subject="${subject}"]`;
    const [, shown] = openPage(page, item.now, [{ click: stop }]);
    const picks = shown!.querySelectorAll("#detail .run-pick").length;
    const entry: { detail: string; rounds?: string[] } = { detail: shown!.querySelector("#detail")!.textContent ?? "" };
    if (picks) {
      const snaps = openPage(page, item.now, [{ click: stop }, ...Array.from({ length: picks }, (_, index) => ({ click: "#detail .run-pick", index }))]);
      entry.rounds = snaps.slice(2).map((snap) => snap.querySelector("#detail .run-card")?.textContent ?? "");
    }
    details[subject] = entry;
  }
  writeFileSync(path.join(dir, "document.json"), `${JSON.stringify(item.document, null, 2)}\n`);
  writeFileSync(path.join(dir, "runs-commands.json"), `${JSON.stringify(recorded, null, 2)}\n`);
  writeFileSync(path.join(dir, "page.html"), page);
  writeFileSync(path.join(dir, "golden.json"), `${JSON.stringify({
    now: item.now, timeZone: "UTC",
    blocks: { "delivery-state": block(page, "delivery-state"), "delivery-runs": block(page, "delivery-runs"), "delivery-links": block(page, "delivery-links") },
    initialText: first!.textContent,
    details,
  }, null, 2)}\n`);
  console.log(item.id, subjects.length, "stops", Object.keys(runs).length, "runs");
}
