// The one place a local test run starts a server: the built Worker in Miniflare, or the built portable Node server.
// The browser run and the conformance runs take their runtime and synthetic repository credentials from here.
import { Miniflare, Log, LogLevel } from "miniflare";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Two synthetic repositories, each with a token of 256 random bits made for this process alone. */
export const credentials = Object.freeze([
  Object.freeze({ repository: "browser-repo", token: randomBytes(32).toString("base64url") }),
  Object.freeze({ repository: "browser-repo-b", token: randomBytes(32).toString("base64url") }),
]);

const sha256 = (value) => createHash("sha256").update(value).digest("hex");

/** The publisher map the Worker's `PUBLISHERS` secret holds for these credentials. */
export function publishers(rows = credentials) {
  return JSON.stringify(Object.fromEntries(rows.map((row) => [row.repository, sha256(row.token)])));
}

export const builtWorker = new URL("../dist/worker.js", import.meta.url);

/**
 * Starts the Worker in a local Miniflare runtime on a free loopback port, with request logging and telemetry off.
 * `script` is the bundled Worker module; it defaults to the dry-run build in `dist/`.
 */
export async function startWorker({ script } = {}) {
  const contents = script ?? readFileSync(builtWorker, "utf8");
  const runtime = new Miniflare({
    host: "127.0.0.1",
    port: 0,
    log: new Log(LogLevel.NONE),
    logRequests: false,
    telemetry: { enabled: false },
    enableDevRegistry: false,
    workers: [
      {
        config: {
          name: "browser-viewer",
          compatibilityDate: "2026-11-15",
          compatibilityFlags: ["nodejs_compat"],
          manifest: {
            mainModule: "worker.js",
            modules: { "worker.js": { type: "esm", contents } },
          },
          exports: {
            DeliveryObject: { type: "durable-object", storage: "sqlite" },
          },
          env: {
            PUBLISHERS: { type: "text", value: publishers() },
            SNAPSHOTS: { type: "r2", name: "SNAPSHOTS" },
            DELIVERIES: {
              type: "durable-object",
              worker: "browser-viewer",
              exportName: "DeliveryObject",
            },
          },
        },
      },
    ],
  });
  let origin;
  try {
    origin = (await runtime.ready).origin;
  } catch (error) {
    await runtime.dispose().catch(() => {});
    throw error;
  }
  return { origin: String(origin).replace(/\/$/, ""), dispose: () => runtime.dispose() };
}

export const builtNode = new URL("../build/node/server.mjs", import.meta.url);

/**
 * Starts the built portable server (`npm run build:node`) as its own process on a free loopback port, with the
 * credentials above in `PUBLISHERS`. With no `dataDir` it serves a fresh temporary one, removed on dispose.
 * The process is stopped by its recorded pid: `stop(signal)` sends one signal and waits for the exit.
 */
/** @param {{ dataDir?: string, env?: Record<string, string | undefined> }} [options] */
export async function startNode({ dataDir, env = {} } = {}) {
  const own = !dataDir;
  const directory = dataDir ?? mkdtempSync(join(tmpdir(), "hosted-node-"));
  const child = spawn(process.execPath, [fileURLToPath(builtNode)], {
    // An override of undefined removes that variable.
    env: Object.fromEntries(Object.entries({ PATH: process.env.PATH, HOST: "127.0.0.1", PORT: "0", DATA_DIR: directory,
      PUBLISHERS: publishers(), ...env }).filter(([, value]) => value !== undefined)),
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  const origin = await new Promise((resolve, reject) => {
    let stdout = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      const match = /listening on (http:\/\/\S+)/.exec(stdout);
      if (match) resolve(match[1]);
    });
    exited.then(({ code, signal }) => reject(Object.assign(new Error(`the portable server exited before listening (${code ?? signal}): ${stderr.trim()}`), { stderr, code })));
  }).catch((error) => { if (own) rmSync(directory, { recursive: true, force: true }); throw error; });
  const pid = child.pid;
  async function stop(signal = "SIGTERM") {
    if (child.exitCode === null && child.signalCode === null) process.kill(pid, signal);
    return exited;
  }
  return {
    origin, pid, dataDir: directory, exited, stop, stderr: () => stderr,
    async dispose() {
      await stop("SIGTERM");
      if (own) rmSync(directory, { recursive: true, force: true });
    },
  };
}

/** The server the run targets: the portable server when HOSTED_TARGET is "node", otherwise the Worker. */
export const target = process.env.HOSTED_TARGET === "node" ? "node" : "worker";
export function startServer() {
  return target === "node" ? startNode() : startWorker();
}
