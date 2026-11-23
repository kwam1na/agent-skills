// The one place a local test run starts the built Worker: the browser run and the conformance run take their
// runtime and their synthetic repository credentials from here.
import { Miniflare, Log, LogLevel } from "miniflare";
import { readFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";

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
