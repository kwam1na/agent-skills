import {
  digest,
  ID,
  MAX_BYTES,
  RENDERER,
  parse,
  projection,
  object,
} from "./protocol";

export interface Metadata {
  protocolVersion: "hosted-delivery-api/1";
  rendererVersion: typeof RENDERER;
  epoch: number;
  sequence: number;
  digest: string;
  sourceFingerprint: string;
  publishedAt: number;
  sourceConfirmedAt: number;
  serverNow: number;
  staleAfterMs: number;
  stale: boolean;
}
export interface ViewStatus {
  kind:
    | "loading"
    | "current"
    | "stale"
    | "retained"
    | "unavailable"
    | "incompatible";
  detail: string;
  receipt: Metadata | null;
  /** Whether the last request failed, though the status it leaves may still be readable. */
  problem: boolean;
}
export interface PublicProjection extends Record<string, unknown> {
  sourceFingerprint: string;
  document: {
    state: Record<string, unknown>;
    journal: Record<string, unknown>[];
  };
  view: Record<string, unknown>;
  links: Record<string, unknown>;
}
type Fetcher = (url: string, init: RequestInit) => Promise<Response>;
class ViewError extends Error {
  constructor(readonly code: "unavailable" | "incompatible") {
    super(code);
  }
}
function metadata(value: unknown): Metadata {
  const row = object(value);
  const number = (key: string) =>
    typeof row[key] === "number" &&
    Number.isSafeInteger(row[key]) &&
    Number(row[key]) >= 0;
  if (
    row.protocolVersion !== "hosted-delivery-api/1" ||
    row.rendererVersion !== RENDERER ||
    ![
      "epoch",
      "sequence",
      "publishedAt",
      "sourceConfirmedAt",
      "serverNow",
    ].every(number) ||
    Number(row.epoch) < 1 ||
    Number(row.sequence) < 1 ||
    typeof row.digest !== "string" ||
    !ID.test(row.digest) ||
    typeof row.sourceFingerprint !== "string" ||
    !ID.test(row.sourceFingerprint) ||
    row.staleAfterMs !== 120000 ||
    typeof row.stale !== "boolean" ||
    Number(row.sourceConfirmedAt) > Number(row.serverNow)
  )
    throw new ViewError("incompatible");
  return {
    protocolVersion: "hosted-delivery-api/1",
    rendererVersion: RENDERER,
    epoch: Number(row.epoch),
    sequence: Number(row.sequence),
    digest: String(row.digest),
    sourceFingerprint: String(row.sourceFingerprint),
    publishedAt: Number(row.publishedAt),
    sourceConfirmedAt: Number(row.sourceConfirmedAt),
    serverNow: Number(row.serverNow),
    staleAfterMs: 120000,
    stale: row.stale === true,
  };
}
function order(a: Metadata, b: Metadata) {
  return a.epoch === b.epoch ? a.sequence - b.sequence : a.epoch - b.epoch;
}
export function isTerminal(value: PublicProjection): boolean {
  let closed = false;
  for (const row of value.document.journal) {
    if (row.kind === "delivery.closed") closed = true;
    else if (
      ["lane.assigned", "candidate.handed", "check.started"].includes(
        String(row.kind),
      )
    )
      closed = false;
  }
  return closed;
}
async function bounded(
  response: Response,
  limit: number,
): Promise<Uint8Array<ArrayBuffer>> {
  if (!response.ok || response.redirected) throw new ViewError("unavailable");
  if (
    response.headers.get("Content-Type")?.split(";")[0] !== "application/json"
  )
    throw new ViewError("incompatible");
  const hinted = response.headers.get("Content-Length");
  if (hinted && Number(hinted) > limit) throw new ViewError("incompatible");
  const reader = response.body?.getReader();
  if (!reader) throw new ViewError("unavailable");
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const row = await reader.read();
      if (row.done) break;
      size += row.value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new ViewError("incompatible");
      }
      chunks.push(row.value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}
function leaksCapability(href: string, publicId: string): boolean {
  let decoded = href;
  for (let i = 0; i < 8; i++) {
    if (decoded.toLowerCase().includes(publicId)) return true;
    let next: string;
    try {
      next = decodeURIComponent(decoded);
    } catch {
      return true;
    }
    if (next === decoded) return false;
    decoded = next;
  }
  return true; // Deeply encoded addresses are not canonical view links.
}
function presentation(
  value: PublicProjection,
  publicId: string,
): PublicProjection {
  const body = structuredClone(value);
  body.view = {
    ...body.view,
    themeKey: "hosted-delivery:theme",
    history: null,
  };
  for (const category of ["issues", "commits", "changes"]) {
    const links = object(body.links[category]);
    for (const [key, target] of Object.entries(links)) {
      const href = category === "changes" ? object(target).href : target;
      if (typeof href !== "string" || leaksCapability(href, publicId))
        delete links[key];
    }
  }
  return body;
}
export function createViewStore(
  publicId: string,
  options: { fetcher?: Fetcher; now?: () => number } = {},
) {
  if (!ID.test(publicId)) throw new ViewError("unavailable");
  const fetcher = options.fetcher ?? fetch.bind(globalThis);
  const now = options.now ?? (() => performance.now());
  let held: PublicProjection | null = null,
    receipt: Metadata | null = null,
    observed: Metadata | null = null,
    receivedAt = 0;
  let problem: "unavailable" | "incompatible" | null = null,
    inFlight: Promise<void> | null = null,
    controller: AbortController | null = null,
    generation = 0;
  let pollTimer: ReturnType<typeof setInterval> | null = null,
    tickTimer: ReturnType<typeof setInterval> | null = null;
  const subscribers = new Set<(doc: PublicProjection["document"]) => void>();
  const statuses = new Set<(value: ViewStatus) => void>();
  const connections = new Set<(live: boolean) => void>();
  function status(): ViewStatus {
    const terminal = !!held && isTerminal(held);
    const newer = observed && receipt && order(observed, receipt) > 0;
    const expired =
      !!receipt &&
      (receipt.stale ||
        receipt.serverNow -
          receipt.sourceConfirmedAt +
          Math.max(0, now() - receivedAt) >=
          120000);
    let kind: ViewStatus["kind"];
    if (problem && !(terminal && !newer && problem === "unavailable"))
      kind = problem === "unavailable" && held && expired ? "stale" : problem;
    else if (!receipt) kind = "loading";
    else if (terminal && !newer) kind = "retained";
    else kind = expired ? "stale" : "current";
    const detail = {
      loading: "Loading hosted progress…",
      current: "Progress confirmed",
      stale: problem
        ? "Progress is stale · connection unavailable; showing the last saved progress"
        : "Progress is stale · publisher contact lost",
      retained: problem
        ? "Saved completed delivery · connection unavailable"
        : "Completed delivery · saved and retained",
      unavailable: held
        ? "Update unavailable · showing the last saved progress"
        : "Hosted progress unavailable",
      incompatible: held
        ? "Incompatible update · showing the last saved progress"
        : "This hosted view requires a compatible snapshot",
    }[kind];
    return { kind, detail, receipt, problem: !!problem };
  }
  function announce() {
    const value = status();
    for (const listener of statuses) listener(value);
    for (const listener of connections)
      listener(value.kind === "current" || value.kind === "retained");
  }
  async function load(signal: AbortSignal, cut: number) {
    const sampledAt = now();
    const init: RequestInit = {
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      referrerPolicy: "no-referrer",
      signal,
    };
    const meta = metadata(
      parse(await bounded(await fetcher(`/d/${publicId}/current`, init), 4096)),
    );
    if (observed && order(meta, observed) < 0)
      throw new ViewError("unavailable");
    if (
      receipt &&
      order(meta, receipt) === 0 &&
      (meta.digest !== receipt.digest ||
        meta.sourceFingerprint !== receipt.sourceFingerprint)
    )
      throw new ViewError("incompatible");
    observed = meta;
    let body = held;
    if (!receipt || meta.digest !== receipt.digest) {
      const bytes = await bounded(
        await fetcher(`/d/${publicId}/snapshots/${meta.digest}`, init),
        MAX_BYTES,
      );
      if ((await digest(bytes)) !== meta.digest)
        throw new ViewError("incompatible");
      const value = parse(bytes);
      try {
        const checked = projection(value);
        if (checked.sourceFingerprint !== meta.sourceFingerprint)
          throw new ViewError("incompatible");
      } catch {
        throw new ViewError("incompatible");
      }
      body = presentation(value as PublicProjection, publicId);
    } else if (meta.sourceFingerprint !== receipt.sourceFingerprint)
      throw new ViewError("incompatible");
    if (signal.aborted || cut !== generation) return;
    const changed =
      !receipt || order(meta, receipt) !== 0 || meta.digest !== receipt.digest;
    held = body;
    receipt = meta;
    receivedAt = sampledAt;
    problem = null;
    if (changed && held)
      for (const listener of subscribers) listener(held.document);
    announce();
  }
  function poll(): Promise<void> {
    if (inFlight) return inFlight;
    controller = new AbortController();
    const current = controller;
    const cut = generation;
    const deadline = setTimeout(() => current.abort(), 10000);
    inFlight = load(current.signal, cut)
      .catch((error) => {
        if (cut !== generation) return;
        problem = error instanceof ViewError ? error.code : "unavailable";
        announce();
      })
      .finally(() => {
        clearTimeout(deadline);
        inFlight = null;
        if (controller === current) controller = null;
      });
    return inFlight;
  }
  return {
    poll,
    status,
    read: () => held?.document ?? null,
    projection: () => held,
    subscribe(listener: (doc: PublicProjection["document"]) => void) {
      subscribers.add(listener);
      return () => subscribers.delete(listener);
    },
    onStatus(listener: (live: boolean) => void) {
      connections.add(listener);
      return () => connections.delete(listener);
    },
    onHostingStatus(listener: (value: ViewStatus) => void) {
      statuses.add(listener);
      listener(status());
      return () => statuses.delete(listener);
    },
    start() {
      if (pollTimer) return;
      void poll();
      pollTimer = setInterval(() => void poll(), 5000);
      tickTimer = setInterval(announce, 1000);
    },
    stop() {
      generation++;
      controller?.abort();
      if (pollTimer) clearInterval(pollTimer);
      if (tickTimer) clearInterval(tickTimer);
      pollTimer = null;
      tickTimer = null;
    },
  };
}
