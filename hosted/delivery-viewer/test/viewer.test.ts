import { JSDOM } from "jsdom";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect, vi } from "vitest";
import { createViewStore, type PublicProjection } from "../src/view-store";
import { cases } from "./fixtures.generated";
import { digest, RENDERER, projection } from "../src/protocol";
const publicId = "a".repeat(64);
const fixture = () =>
  structuredClone(cases.find((row) => row.valid)!.body) as PublicProjection;
async function revision(
  sequence = 1,
  body: PublicProjection = fixture(),
  now = 1000000,
) {
  const bytes = new TextEncoder().encode(JSON.stringify(body));
  return {
    body,
    bytes,
    meta: {
      protocolVersion: "hosted-delivery-api/1",
      rendererVersion: RENDERER,
      epoch: 1,
      sequence,
      digest: await digest(bytes),
      sourceFingerprint: (body as { sourceFingerprint: string })
        .sourceFingerprint,
      publishedAt: 1000000,
      sourceConfirmedAt: 1000000,
      serverNow: now,
      staleAfterMs: 120000,
      stale: now >= 1120000,
    },
  };
}
describe("hosted view store", () => {
  it("loads verified data, coalesces polls and never sends publishing credentials", async () => {
    const r = await revision();
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    const fetcher = vi.fn(async (url: string, init: RequestInit) => {
      expect(init.credentials).toBe("omit");
      expect(init.referrerPolicy).toBe("no-referrer");
      expect(init.redirect).toBe("error");
      expect(init.headers).toBeUndefined();
      await held;
      return url.endsWith("/current")
        ? Response.json(r.meta)
        : new Response(r.bytes, {
            headers: { "Content-Type": "application/json" },
          });
    });
    const store = createViewStore(publicId, { fetcher });
    const first = store.poll();
    const second = store.poll();
    release();
    await Promise.all([first, second]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(store.read()).toEqual(r.body.document);
    expect(store.status().kind).toBe("current");
  });
  it("retains prior data across metadata, digest, pointer, schema and renderer failures", async () => {
    const good = await revision();
    const changed = fixture();
    changed.document.state.meta = { headline: "Changed" };
    const next = await revision(2, changed);
    const future = await revision(2, { ...fixture(), schemaVersion: "future" });
    const older = await revision(2, { ...fixture(), schemaVersion: "hosted-delivery-snapshot/1" });
    let mode = "good";
    const store = createViewStore(publicId, {
      fetcher: async (url) => {
        if (mode === "network") throw Error("private-url-canary");
        if (url.endsWith("/current"))
          return Response.json(
            mode === "renderer"
              ? { ...next.meta, rendererVersion: "future" }
              : mode === "previous renderer"
                ? { ...next.meta, rendererVersion: "delivery-line-map/1" }
              : mode === "good"
                ? good.meta
                : mode === "schema"
                  ? future.meta
                  : mode === "older"
                    ? older.meta
                    : next.meta,
          );
        if (mode === "404") return new Response("", { status: 404 });
        if (mode === "digest") return Response.json(good.body);
        if (mode === "schema") return Response.json(future.body);
        if (mode === "older") return Response.json(older.body);
        return Response.json(mode === "good" ? good.body : next.body);
      },
    });
    await store.poll();
    const before = store.projection();
    for (const failure of ["network", "renderer", "previous renderer", "404", "digest", "schema", "older"]) {
      mode = failure;
      await store.poll();
      expect(store.projection()).toBe(before);
      expect(store.status().kind).toMatch(failure === "older" ? /^incompatible$/ : /unavailable|incompatible/);
      expect(JSON.stringify(store.status())).not.toContain(
        "private-url-canary",
      );
    }
  });
  it("uses server source age plus monotonic elapsed time, and exempts only acknowledged terminal cuts", async () => {
    let elapsed = 0;
    let r = await revision(1, fixture(), 1119999);
    const store = createViewStore(publicId, {
      now: () => elapsed,
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : Response.json(r.body),
    });
    await store.poll();
    expect(store.status().kind).toBe("current");
    elapsed = 1;
    expect(store.status().kind).toBe("stale");
    const done = fixture();
    done.document.journal.push({
      id: 2,
      at: "2026-11-15T00:01:00Z",
      kind: "delivery.closed",
      lane: "lead",
      subject: "APP-1",
    });
    r = await revision(2, done, 9999999);
    await store.poll();
    expect(store.status().kind).toBe("retained");
    done.document.journal.push({
      id: 3,
      at: "2026-11-15T00:02:00Z",
      kind: "lane.assigned",
      lane: "one",
      subject: "APP-1",
    });
    r = await revision(3, done, 9999999);
    await store.poll();
    expect(store.status().kind).toBe("stale");
  });
  it("does not regress revisions, mismatch fingerprint or accept an incompatible schema", async () => {
    let r = await revision(2);
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    const before = store.projection();
    r = await revision(1);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("unavailable");
    expect(store.status().receipt?.sequence).toBe(2);
    r = await revision(3);
    r.meta.sourceFingerprint = "f".repeat(64);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
    expect(store.status().receipt?.sequence).toBe(2);
    const changed = fixture();
    changed.document.state.meta = { headline: "Unmatched source cut" };
    r = await revision(3, changed);
    r.meta.sourceFingerprint = "f".repeat(64);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
    expect(store.status().receipt?.sequence).toBe(2);
    const future = { ...fixture(), schemaVersion: "future" };
    r = await revision(3, future);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
    // A body in the older shape is refused as well, never half-read: the prior projection stands.
    const older = { ...fixture(), schemaVersion: "hosted-delivery-snapshot/1" };
    r = await revision(3, older);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
  });
});

describe("trusted corpus DOM bridge (behavior only)", () => {
  it("preserves selection and folds while applying plan, review and document data together", async () => {
    const built = JSON.parse(
      readFileSync(new URL("./assets.generated.json", import.meta.url), "utf8"),
    ) as { renderer: string };
    const initial = fixture();
    let held = initial;
    let listener: (doc: PublicProjection["document"]) => void = () => {};
    const dom = new JSDOM(
      '<!doctype html><body><main id="app"></main></body>',
      {
        url: `https://service.test/d/${publicId}`,
        runScripts: "outside-only",
        pretendToBeVisual: true,
      },
    );
    const { window } = dom;
    Object.assign(window, {
      matchMedia: () => ({ matches: true }),
      scrollTo: () => {},
      fetch: vi.fn(() => Promise.reject(Error("unexpected HTML refresh"))),
      deliveryStateStore: {
        projection: () => held,
        read: () => held.document,
        subscribe: (cb: typeof listener) => {
          listener = cb;
        },
        onStatus: () => {},
      },
    });
    for (const [key, value] of Object.entries({
      state: initial.document,
      runs: initial.runs,
      reviews: initial.reviews,
      links: initial.links,
      view: initial.view,
    })) {
      const node = window.document.createElement("script");
      node.id = `delivery-${key}`;
      node.type = "application/json";
      node.textContent = JSON.stringify(value);
      window.document.body.appendChild(node);
    }
    try {
      // Every chapter on one page, the reader's other layout: paged, a click in the plan's fold opens the plan's page,
      // and the stop on Now is rightly no longer where focus is. Pushes keep selection, folds and focus either way.
      window.localStorage.setItem("hosted-delivery:layout", "scroll");
      window.eval(built.renderer);
      expect(window.document.querySelector("#plan")).toBeNull();
      const button =
        window.document.querySelector<HTMLButtonElement>(".stop-btn");
      expect(button).not.toBeNull();
      button!.focus();
      button!.click();
      const next = fixture();
      next.plan = structuredClone(
        cases.find((row) => row.file === "valid-plan-review.json")!.body.plan,
      );
      next.document.state.meta = { headline: "Coherent new headline" };
      held = next;
      listener(next.document);
      expect(window.document.querySelector("#app")!.textContent).toContain(
        "Coherent new headline",
      );
      // A record with no story reads as it does on the local page.
      expect(window.document.querySelector("#app")!.textContent).toContain(
        "The story could not be told from this record.",
      );
      expect(window.document.querySelector("#app")!.textContent).not.toContain(
        "not published",
      );
      // Nor a setbacks ledger or a chapter's setback line: with no story there is no ledger to draw, and no reason leaks.
      expect(
        window.document.querySelectorAll("#happened .setback-ledger, #happened .chapter-setback").length,
      ).toBe(0);
      expect(window.document.querySelector("#plan")).not.toBeNull();
      const detail = window.document.querySelector("#detail")!.textContent;
      expect(detail).toContain("APP-1");
      const fold = Array.from(window.document.querySelectorAll("summary")).find(
        (row) => row.textContent?.includes("All units"),
      )!.parentElement as HTMLDetailsElement;
      // The page remembers a fold at its summary's click, as a reader opens it.
      (fold.querySelector("summary") as HTMLElement).click();
      held = structuredClone(next);
      listener(held.document);
      expect(
        Array.from(window.document.querySelectorAll("summary"))
          .find((row) => row.textContent?.includes("All units"))!
          .parentElement!.hasAttribute("open"),
      ).toBe(true);
      expect(window.document.activeElement?.getAttribute("data-subject")).toBe(
        "APP-1",
      );
      held = structuredClone(next);
      held.plan = null;
      listener(held.document);
      expect(
        (window.document.querySelector("#plan") as HTMLElement).hidden,
      ).toBe(true);
      expect(window.fetch).not.toHaveBeenCalled();
    } finally {
      window.close();
    }
  });
});

describe("a review note's file reference on the hosted page", () => {
  /** The corpus renderer over one projection, its story's detail opened, as the hosted page draws it. */
  function drawn(body: PublicProjection) {
    const built = JSON.parse(
      readFileSync(new URL("./assets.generated.json", import.meta.url), "utf8"),
    ) as { renderer: string };
    const dom = new JSDOM('<!doctype html><body><main id="app"></main></body>', {
      url: `https://service.test/d/${publicId}`,
      runScripts: "outside-only",
      pretendToBeVisual: true,
    });
    const { window } = dom;
    Object.assign(window, {
      matchMedia: () => ({ matches: true }),
      scrollTo: () => {},
      fetch: vi.fn(() => Promise.reject(Error("unexpected HTML refresh"))),
      deliveryStateStore: { projection: () => body, read: () => body.document, subscribe: () => {}, onStatus: () => {} },
    });
    for (const [key, value] of Object.entries({ state: body.document, runs: body.runs, reviews: body.reviews,
      links: body.links, view: body.view })) {
      const node = window.document.createElement("script");
      node.id = `delivery-${key}`;
      node.type = "application/json";
      node.textContent = JSON.stringify(value);
      window.document.body.appendChild(node);
    }
    window.eval(built.renderer);
    window.document.querySelector<HTMLButtonElement>('.stop-btn[data-subject="APP-1"]')!.click();
    return dom;
  }
  function cited(): PublicProjection {
    const body = fixture();
    // The page reads its runs, so a story's detail draws its run's rounds.
    body.view.runs = true;
    const runs = body.runs as Record<string, { roundDetail: { detailsUnavailable?: boolean; raised: { summary: string }[] }[] }>;
    // A round whose details were read, so its finding is drawn.
    delete runs["run-1"].roundDetail[0].detailsUnavailable;
    runs["run-1"].roundDetail[0].raised[0].summary = "Guard it (src/app/main.py:4).";
    return body;
  }
  it("opens the file at its line, as the local page does, when the cut carries its link", () => {
    const body = cited();
    const href = `https://github.com/example/app/blob/${"a".repeat(40)}/src/app/main.py#L4`;
    body.links.files = { [`${"a".repeat(40)} src/app/main.py:4`]: href };
    const dom = drawn(body);
    try {
      const ref = dom.window.document.querySelector<HTMLAnchorElement>("#detail a.prose-ref")!;
      expect(ref).not.toBeNull();
      expect(ref.textContent).toBe("src/app/main.py:4");
      expect(ref.getAttribute("href")).toBe(href);
    } finally {
      dom.window.close();
    }
  });
  it("shows the reference as words when the cut carries no file links", () => {
    const dom = drawn(cited());
    try {
      const detail = dom.window.document.querySelector("#detail")!;
      expect(detail.textContent).toContain("src/app/main.py:4");
      expect(detail.querySelector("a.prose-ref")).toBeNull();
      expect(detail.querySelector(".prose-ref")).not.toBeNull();
    } finally {
      dom.window.close();
    }
  });
});

describe("hosted adapter allow and fail boundaries", () => {
  it("publishes a coherent new projection before notifying its document subscribers", async () => {
    let r = await revision();
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    const next = fixture();
    next.document.state.meta = { headline: "New coherent progress" };
    next.plan = structuredClone(
      cases.find((row) => row.file === "valid-plan-review.json")!.body.plan,
    );
    r = await revision(2, next);
    let observed: PublicProjection | null = null;
    store.subscribe(() => {
      observed = store.projection();
    });
    await store.poll();
    expect(observed).toBe(store.projection());
    expect(observed).toMatchObject({
      document: next.document,
      plan: next.plan,
    });
    expect(store.status().kind).toBe("current");
  });
  it("removes capability-bearing external URLs while retaining canonical links", async () => {
    const value = fixture();
    value.links = {
      issues: {
        ordinary: "https://example.com/issue/APP-1",
        direct: `https://example.com/${publicId}`,
        encoded: `https://example.com/${publicId
          .split("")
          .map((c) => "%" + c.charCodeAt(0).toString(16))
          .join("")}`,
      },
      commits: {},
      changes: {},
    };
    const r = await revision(1, value);
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    expect(store.projection()?.links.issues).toEqual({
      ordinary: "https://example.com/issue/APP-1",
    });
  });
  it("keeps a file link, and drops one that would carry the view's own capability", async () => {
    const value = fixture();
    const sha = "a".repeat(40);
    value.links.files = {
      [`${sha} src/app/main.py:4`]: `https://github.com/example/app/blob/${sha}/src/app/main.py#L4`,
      [`${sha} src/app/leak.py:1`]: `https://example.com/${publicId}/leak.py#L1`,
    };
    const r = await revision(1, value);
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    expect(store.projection()?.links.files).toEqual({
      [`${sha} src/app/main.py:4`]: `https://github.com/example/app/blob/${sha}/src/app/main.py#L4`,
    });
  });
  it("accounts for time spent downloading a snapshot without refreshing source freshness", async () => {
    let elapsed = 0;
    const r = await revision(1, fixture(), 1119999);
    const store = createViewStore(publicId, {
      now: () => elapsed,
      fetcher: async (url) => {
        if (url.endsWith("/current")) return Response.json(r.meta);
        elapsed = 1000;
        return new Response(r.bytes, {
          headers: { "Content-Type": "application/json" },
        });
      },
    });
    await store.poll();
    expect(store.status().kind).toBe("stale");
  });
  it("bounds hung polls, uses the five-second cadence and cancels its own timers on stop", async () => {
    vi.useFakeTimers();
    try {
      const fetcher = vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener(
              "abort",
              () => reject(Error("aborted")),
              { once: true },
            );
          }),
      );
      const store = createViewStore(publicId, { fetcher });
      store.start();
      await vi.advanceTimersByTimeAsync(9999);
      expect(fetcher).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(store.status().kind).toBe("unavailable");
      await vi.advanceTimersByTimeAsync(5000);
      expect(fetcher).toHaveBeenCalledTimes(2);
      store.stop();
      await vi.advanceTimersByTimeAsync(20000);
      expect(fetcher).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("disconnected expiry", () => {
  it("marks retained open progress stale after source expiry even when polling keeps failing", async () => {
    let elapsed = 0,
      down = false;
    const r = await revision(1, fixture(), 1119999);
    const store = createViewStore(publicId, {
      now: () => elapsed,
      fetcher: async (url) => {
        if (down) throw Error("offline");
        return url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            });
      },
    });
    await store.poll();
    down = true;
    await store.poll();
    expect(store.status().kind).toBe("unavailable");
    elapsed = 1;
    expect(store.status().kind).toBe("stale");
    expect(store.read()).toEqual(r.body.document);
  });
});

describe("independent polling and byte budgets", () => {
  it("polls successful current metadata at exactly the five-second cadence", async () => {
    const r = await revision();
    vi.useFakeTimers();
    try {
      const fetcher = vi.fn(async (url: string) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
      );
      const store = createViewStore(publicId, { fetcher });
      await store.poll();
      store.start();
      await store.poll();
      const count = fetcher.mock.calls.length;
      await vi.advanceTimersByTimeAsync(4999);
      expect(fetcher).toHaveBeenCalledTimes(count);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetcher).toHaveBeenCalledTimes(count + 1);
      store.stop();
    } finally {
      vi.useRealTimers();
    }
  });
  it("bounds actual metadata and projection bytes despite a dishonest length header", async () => {
    let r = await revision();
    let oversizedMeta = false;
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? new Response(
              JSON.stringify(r.meta).padEnd(oversizedMeta ? 4097 : 0, " "),
              {
                headers: {
                  "Content-Type": "application/json",
                  "Content-Length": "2",
                },
              },
            )
          : new Response(r.bytes, {
              headers: {
                "Content-Type": "application/json",
                "Content-Length": "2",
              },
            }),
    });
    await store.poll();
    const before = store.projection();
    oversizedMeta = true;
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
    oversizedMeta = false;
    const bytes = new TextEncoder().encode(
      JSON.stringify(fixture()).padEnd(16 * 1024 * 1024 + 1, " "),
    );
    r = { ...(await revision(2)), bytes };
    r.meta.digest = await digest(bytes);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
  });
});

function auxiliaryProjection(label: string): PublicProjection {
  const body = fixture();
  body.view.runs = true;
  body.plan = structuredClone(
    cases.find((row) => row.file === "valid-plan-review.json")!.body.plan,
  );
  body.view.lenses = {
    "lens.security": { name: `${label} reviewer`, icon: "spy" },
  };
  (body.document.state.queue as Record<string, unknown>[]).push({
    subject: "APP-2",
    title: "Native evidence",
    lane: "one",
    stage: "queued",
    head: "b".repeat(40),
  });
  body.document.journal.push({
    id: 2,
    at: "2026-11-15T00:01:00Z",
    kind: "review.round",
    lane: "one",
    subject: "APP-2",
    data: {
      round: 1,
      verdicts: { "lens.security": "changes" },
      findings: { P0: 0, P1: 1, P2: 0, P3: 0 },
    },
  });
  // Every kept review journals its capture after its round, with the digest and candidate the page checks it by.
  (body.document.journal as Record<string, unknown>[]).push({
    id: 3,
    at: "2026-11-15T00:01:30Z",
    kind: "review.recorded",
    lane: "one",
    subject: "APP-2",
    data: { digest: "d".repeat(64), candidateRef: "b".repeat(40) },
  });
  const round = {
    round: 1,
    candidateRef: "a".repeat(40),
    at: "2026-11-15T00:00:00Z",
    outcome: "changes",
    verdicts: { "lens.security": "changes" },
    findings: { P0: 0, P1: 1, P2: 0, P3: 0 },
    raised: [
      {
        id: "f1",
        severity: "P1",
        lens: "lens.security",
        summary: `${label} run finding`,
        state: "unresolved",
      },
    ],
  };
  const latestRound = {
    ...round,
    round: 2,
    outcome: "approve",
    verdicts: { "lens.security": "approve" },
    findings: { P0: 0, P1: 0, P2: 1, P3: 0 },
    raised: [
      {
        ...round.raised[0],
        id: "f2",
        severity: "P2",
        filed: "deferred",
        summary: `${label} run finding`,
      },
    ],
  };
  body.runs = {
    "run-1": {
      runId: "run-1",
      open: false,
      roundDetail: label === "new" ? [round, latestRound] : [round],
    },
  };
  body.reviews = {
    "APP-2": {
      observational: true,
      observationKey: "APP-2",
      candidateRef: "b".repeat(40),
      journalId: 3,
      digest: "d".repeat(64),
      roundDetail: [
        {
          ...round,
          candidateRef: "b".repeat(40),
          raised: [{ ...round.raised[0], summary: `${label} native finding` }],
        },
      ],
    },
  };
  body.links = {
    issues: { "APP-1": `https://example.com/${label}` },
    changes: {},
    commits: {},
  };
  body.needs = {
    running: true,
    at: new Date().toISOString(),
    staleAfter: new Date(Date.now() + 600000).toISOString(),
    items: [
      {
        holding: true,
        source: "operator",
        text: `${label} needs line`,
        clears: "Recorded resolution",
      },
    ],
  };
  return body;
}
function seedDOM(window: JSDOM["window"], body: PublicProjection) {
  for (const [key, value] of Object.entries({
    state: body.document,
    runs: body.runs,
    reviews: body.reviews,
    links: body.links,
    view: body.view,
    plan: body.plan,
    needs: body.needs,
    files: body.files,
    "left-open": body.leftOpen,
    integration: body.integration,
  })) {
    if (value === null || value === undefined) continue;
    const block = window.document.createElement("script");
    block.type = "application/json";
    block.id = `delivery-${key}`;
    block.textContent = JSON.stringify(value);
    window.document.body.appendChild(block);
  }
}
function testDOM() {
  const dom = new JSDOM(
    '<!doctype html><body><p id="hosting-status" class="hosting-status hosted-announce" role="status" aria-live="polite">Loading hosted progress…</p><main id="app"></main><script data-renderer="/assets/renderer.js"></script></body>',
    {
      url: `https://service.test/d/${publicId}`,
      runScripts: "outside-only",
      pretendToBeVisual: true,
    },
  );
  Object.assign(dom.window, {
    matchMedia: () => ({ matches: true }),
    scrollTo: () => {},
    TextEncoder,
    TextDecoder,
    Response,
    AbortController,
    structuredClone,
  });
  Object.defineProperty(dom.window, "crypto", { value: globalThis.crypto });
  return dom;
}
describe("the hosted page draws what the local page draws", () => {
  /** The auxiliary projection with: APP-2 stopped by a journaled stop with its facts, APP-3 stopped with no stop
   * journaled, APP-4 in a check run a decision was recorded during, a lane blocked on nothing named, and the main
   * line broken. */
  function hostedBody(): PublicProjection {
    const body = auxiliaryProjection("old");
    const state = body.document.state as Record<string, unknown>;
    const queue = state.queue as Record<string, unknown>[];
    queue[queue.length - 1].stage = "stopped";
    queue.push(
      { subject: "APP-3", title: "Never journaled", lane: "one", stage: "stopped", head: "c".repeat(40) },
      { subject: "APP-4", title: "In its checks", lane: "one", stage: "checking", head: "d".repeat(40) },
    );
    state.lanes = [{ id: "two", title: "Lane two", focus: "Waiting", state: "blocked", waitingOn: null, since: "2026-11-15T00:00:00Z" }];
    state.meta = { ...(state.meta as Record<string, unknown>), checking: { now: { subject: "APP-4", title: "In its checks",
      startedAt: "2026-11-15T00:05:00Z", steps: [{ label: "suite", state: "failed" }] }, next: [] } };
    (body.document.journal as Record<string, unknown>[]).push(
      { id: 4, at: "2026-11-15T00:02:00Z", kind: "check.started", lane: "one", subject: "APP-2", data: { head: "b".repeat(40) } },
      { id: 5, at: "2026-11-15T00:03:00Z", kind: "candidate.stopped", lane: "one", subject: "APP-2",
        data: { reason: "The suite failed in tests/test_parser.py at its head." } },
      { id: 6, at: "2026-11-15T00:04:00Z", kind: "base.broken", lane: null, subject: null, data: { reason: "fullHealth failed" } },
      { id: 7, at: "2026-11-15T00:05:00Z", kind: "check.started", lane: "one", subject: "APP-4", data: { head: "d".repeat(40) } },
      { id: 8, at: "2026-11-15T00:06:00Z", kind: "decision.recorded", lane: null, subject: "APP-4",
        data: { text: "Rerun it under scripts/run-tests.py", why: "The first run timed out." } },
    );
    return body;
  }
  const fullRecord = () =>
    structuredClone(cases.find((row) => row.file === "valid-full-record.json")!.body) as PublicProjection;
  /** The built page drawn from `body`: as the hosted page, under its store, or as the local page, with none. */
  function drawn(body: PublicProjection, hosted: boolean) {
    const built = JSON.parse(
      readFileSync(new URL("./assets.generated.json", import.meta.url), "utf8"),
    ) as { renderer: string };
    const dom = testDOM();
    if (hosted) {
      Object.assign(dom.window, {
        deliveryStateStore: { projection: () => body, read: () => body.document, subscribe: () => {}, onStatus: () => {} },
      });
    }
    seedDOM(dom.window, body);
    dom.window.eval(built.renderer);
    return dom;
  }
  /** Every chapter's words, less the hero's live line, which says how the page is connected rather than the record. */
  function chapters(dom: JSDOM) {
    const app = dom.window.document.querySelector("#app")!.cloneNode(true) as HTMLElement;
    app.querySelectorAll(".fact.since").forEach((node) => node.remove());
    return app.textContent!.replace(/\s+/g, " ").trim();
  }
  it.each([
    ["a record with facts, stops, checks and a decision", hostedBody],
    ["the full record on an integration base", fullRecord],
  ])("draws every chapter as the local page does: %s", (_name, make) => {
    const body = make();
    projection(body);
    const local = drawn(body, false);
    const hosted = drawn(body, true);
    try {
      const words = chapters(hosted);
      expect(words.length).toBeGreaterThan(400);
      expect(words).toBe(chapters(local));
      expect(words).not.toMatch(/not published|Local reference omitted/);
    } finally {
      local.window.close();
      hosted.window.close();
    }
  });
  it("draws a planning cut the publisher builds, its Seats, Feedback and plan as the local page does", () => {
    // The cut hosted.collect would publish for the corpus's planning record, built by this commit's publisher.
    const built = execFileSync("python3", ["-B", fileURLToPath(new URL("./node/planning_cut.py", import.meta.url))], {
      encoding: "utf8",
    });
    const body = JSON.parse(built) as PublicProjection;
    projection(body);
    const local = drawn(body, false);
    const hosted = drawn(body, true);
    try {
      expect(chapters(hosted)).toBe(chapters(local));
      const page = hosted.window.document;
      expect(page.querySelector("#seats .title")!.textContent).toBe("2 seats write this delivery.");
      expect(page.querySelector("#feedback .title")!.textContent).toBe("2 open, 1 blocking · 4 raised on 3 sections.");
      expect(page.querySelector("#plan .plan-approval")!.textContent).toBe(
        "Agent review only · 2 review rounds · every lens aligned · endorsed by Ama · stale endorsement by Ben <b>B</b> · not approved by a person",
      );
      // The record's markup is text on the hosted page too.
      expect(page.querySelectorAll("#feedback script, #feedback img, #seats b").length).toBe(0);
    } finally {
      local.window.close();
      hosted.window.close();
    }
  });
  /** The full record with region claims, a files view, a drift and, when `closed`, what it left open. */
  function blocksRecord(moved: number, paths: string[], closed = false): PublicProjection {
    const body = fullRecord() as PublicProjection & Record<string, any>;
    const state = body.document.state as Record<string, any>;
    state.laneOwnership = { "lane-1": ["src/*", "docs/*"] };
    state.laneRegions = [{ lane: "lane-1", path: "src/shared.py", baseHead: "a".repeat(40), start: 1, end: 20 }];
    body.files = { spec: "delivery-lane-changes/1", at: "2026-10-30T07:39:00Z", base: "b".repeat(40),
      lanes: { "lane-1": { story: "A", branch: "claude/A", head: "c".repeat(40), base: "b".repeat(40),
        mergeBase: "b".repeat(40), readAt: "2026-10-30T07:39:00Z",
        files: paths.map((path) => ({ path, holds: "file", changes: [] })) } },
      files: paths.map((path) => ({ path, lines: 10, lanes: ["lane-1"] })) };
    body.integration = { drift: { moved, behind: 1 } };
    if (closed) {
      const journal = body.document.journal as Record<string, unknown>[];
      journal.push({ id: journal.length + 100, at: "2026-10-30T08:00:00Z", kind: "delivery.closed", lane: null, subject: null, data: {} });
      body.leftOpen = { heading: "What it left open", rows: [{ subject: "A", said: { headline: "Work A was left stopped", why: "Its suite never passed." } }] };
    }
    projection(body);
    return body;
  }
  it("reads the files view, the drift and what a closed delivery left open from the viewer's seed and its store's pushes", async () => {
    const assets = JSON.parse(readFileSync(new URL("./assets.generated.json", import.meta.url), "utf8")) as
      { renderer: string; viewer: string };
    let served = await revision(1, blocksRecord(3, ["src/one.py"]));
    const dom = testDOM();
    Object.assign(dom.window, {
      fetch: async (url: string) => url.endsWith("/current") ? Response.json(served.meta)
        : new Response(served.bytes, { headers: { "Content-Type": "application/json" } }),
    });
    const words = () => dom.window.document.querySelector("#app")!.textContent!.replace(/\s+/g, " ");
    const files = () => [...dom.window.document.querySelectorAll("details.files-fold [data-path]")]
      .map((node) => node.getAttribute("data-path"));
    try {
      dom.window.eval(assets.viewer);
      await vi.waitFor(() => expect(Reflect.get(dom.window, "deliveryStateStore")).toBeDefined());
      const store = Reflect.get(dom.window, "deliveryStateStore") as ReturnType<typeof createViewStore>;
      dom.window.eval(assets.renderer);
      // The first draw reads the blocks the viewer seeded, as the local page reads the ones it embeds.
      await vi.waitFor(() => expect(words()).toContain("main moved 3 commits while this worked"));
      expect(dom.window.document.querySelector("details.files-fold summary")!.textContent).toBe("Who holds which files");
      expect(files()).toContain("src/one.py");
      const local = drawn(served.body, false);
      try {
        expect(chapters(dom)).toBe(chapters(local));
      } finally {
        local.window.close();
      }
      // A pushed projection moves each block, as a rewritten local page does.
      served = await revision(2, blocksRecord(5, ["src/one.py", "docs/new.md"]));
      await store.poll();
      await vi.waitFor(() => expect(words()).toContain("main moved 5 commits while this worked"));
      expect(files()).toContain("docs/new.md");
      served = await revision(3, blocksRecord(5, ["src/one.py"], true));
      await store.poll();
      await vi.waitFor(() => expect(words()).toContain("Work A was left stopped"));
      expect(dom.window.document.querySelector(".left-open .ah")!.textContent).toBe("What it left open");
    } finally {
      dom.window.dispatchEvent(new dom.window.Event("pagehide"));
      dom.window.close();
    }
  });
  it("draws the integration base, the rejoin bar's four stops, and the story's words", () => {
    const body = fullRecord();
    projection(body);
    const hosted = drawn(body, true);
    try {
      const words = chapters(hosted);
      expect(words).toContain("Rejoin main");
      // The rejoin is drawn as the line's bar, one segment per step, as the local page draws it; the old
      // "Before it rejoins" table is gone from both.
      const steps = [...hosted.window.document.querySelectorAll("#line .mg-step")].map((step) => step.getAttribute("data-step"));
      expect(steps).toEqual(["merged", "suite", "round", "requested"]);
      expect(words).not.toContain("Before it rejoins");
      expect(words).toContain("integrate/app");
      const story = (body.document as Record<string, unknown>).story as Record<string, unknown>;
      expect(story).toBeTruthy();
    } finally {
      hosted.window.close();
    }
  });
});
describe("initial and active coherent rendering", () => {
  it("updates auxiliary-only run, native review, link, needs and lens data in the active DOM", () => {
    const built = JSON.parse(
      readFileSync(new URL("./assets.generated.json", import.meta.url), "utf8"),
    ) as { renderer: string };
    const initial = auxiliaryProjection("old");
    projection(initial);
    let held = initial;
    let listener: (doc: PublicProjection["document"]) => void = () => {};
    const dom = testDOM();
    Object.assign(dom.window, {
      deliveryStateStore: {
        projection: () => held,
        read: () => held.document,
        subscribe: (cb: typeof listener) => {
          listener = cb;
        },
        onStatus: () => {},
      },
    });
    seedDOM(dom.window, initial);
    try {
      dom.window.eval(built.renderer);
      expect(
        dom.window.document.querySelector("#detail")!.textContent,
      ).toContain("old run finding");
      (
        dom.window.document.querySelector(
          '.stop-btn[data-subject="APP-2"]',
        ) as HTMLButtonElement
      ).click();
      expect(
        dom.window.document.querySelector("#detail")!.textContent,
      ).toContain("old native finding");
      const fold = Array.from(
        dom.window.document.querySelectorAll("#plan summary"),
      ).find((row) => row.textContent?.includes("All units"))!
        .parentElement as HTMLDetailsElement;
      // The page remembers a fold at its summary's click, as a reader opens it.
      (fold.querySelector("summary") as HTMLElement).click();
      held = auxiliaryProjection("new");
      projection(held);
      listener(held.document);
      expect(
        dom.window.document
          .querySelector('.stop-btn[data-subject="APP-2"]')!
          .getAttribute("aria-pressed"),
      ).toBe("true");
      expect(
        dom.window.document.querySelector("#detail")!.textContent,
      ).toContain("new native finding");
      expect(
        Array.from(dom.window.document.querySelectorAll("#plan summary"))
          .find((row) => row.textContent?.includes("All units"))!
          .parentElement!.hasAttribute("open"),
      ).toBe(true);
      expect(dom.window.document.querySelector("#now")!.textContent).toContain(
        "new needs line",
      );
      expect(
        dom.window.document
          .querySelector('a[data-ticket="APP-1"]')!
          .getAttribute("href"),
      ).toBe("https://example.com/new");
      (
        dom.window.document.querySelector(
          '.stop-btn[data-subject="APP-1"]',
        ) as HTMLButtonElement
      ).click();
      expect(
        dom.window.document.querySelector("#detail")!.textContent,
      ).toContain("new run finding");
      expect(
        dom.window.document.querySelector("#detail")!.textContent,
      ).toContain("Approved with 1 deferrable note");
      expect(
        dom.window.document.querySelector("#detail")!.textContent,
      ).toContain("new reviewer");
    } finally {
      dom.window.close();
    }
  });
  it.each(["present", "null", "omitted"])(
    "seeds the latest coherent projection while renderer loading (optional plan: %s)",
    async (planMode) => {
      const built = JSON.parse(
        readFileSync(
          new URL("./assets.generated.json", import.meta.url),
          "utf8",
        ),
      ) as { renderer: string; viewer: string };
      const initial = auxiliaryProjection("old");
      initial.plan = structuredClone(
        cases.find((row) => row.file === "valid-plan-review.json")!.body.plan,
      );
      let r = await revision(1, initial);
      const dom = testDOM();
      Object.assign(dom.window, {
        fetch: async (url: string) =>
          url.endsWith("/current")
            ? Response.json(r.meta)
            : new Response(r.bytes, {
                headers: { "Content-Type": "application/json" },
              }),
      });
      try {
        dom.window.eval(built.viewer);
        await vi.waitFor(() =>
          expect(Reflect.get(dom.window, "deliveryStateStore")).toBeDefined(),
        );
        const store = Reflect.get(
          dom.window,
          "deliveryStateStore",
        ) as ReturnType<typeof createViewStore>;
        const next = auxiliaryProjection("new");
        next.document.state.meta = { headline: "Latest startup document" };
        next.plan =
          planMode === "present" ? structuredClone(initial.plan) : null;
        if (planMode === "omitted") delete next.plan;
        if (next.plan)
          (
            next.plan as { plan: { outcome: { summary: string } } }
          ).plan.outcome.summary = "Latest startup plan";
        r = await revision(2, next);
        if (planMode === "omitted")
          expect(
            JSON.parse(new TextDecoder().decode(r.bytes)),
          ).not.toHaveProperty("plan");
        await store.poll();
        dom.window.eval(built.renderer);
        await vi.waitFor(() =>
          expect(
            dom.window.document.querySelector("#app")!.textContent,
          ).toContain("Latest startup document"),
        );
        expect(
          dom.window.document.querySelectorAll("#delivery-state"),
        ).toHaveLength(1);
        expect(
          dom.window.document.querySelector("#detail")!.textContent,
        ).toContain("new run finding");
        expect(
          dom.window.document.querySelector("#now")!.textContent,
        ).toContain("new needs line");
        if (planMode !== "present") {
          expect(
            dom.window.document.querySelector("#delivery-plan"),
          ).toBeNull();
          expect(dom.window.document.querySelector("#plan")).toBeNull();
        } else
          expect(
            dom.window.document.querySelector("#plan")!.textContent,
          ).toContain("Latest startup plan");
      } finally {
        dom.window.dispatchEvent(new dom.window.Event("pagehide"));
        dom.window.close();
      }
    },
  );
});

describe("fenced presentation boundaries", () => {
  it.each(["lane.assigned", "candidate.handed", "check.started"])(
    "does not retain completion after %s reopens work",
    async (kind) => {
      const value = fixture();
      value.document.journal.push({
        id: 2,
        at: "2026-11-15T00:01:00Z",
        kind: "delivery.closed",
        lane: null,
        subject: null,
      });
      let r = await revision(1, value, 9999999);
      const store = createViewStore(publicId, {
        fetcher: async (url) =>
          url.endsWith("/current")
            ? Response.json(r.meta)
            : new Response(r.bytes, {
                headers: { "Content-Type": "application/json" },
              }),
      });
      await store.poll();
      expect(store.status().kind).toBe("retained");
      value.document.journal.push({
        id: 3,
        at: "2026-11-15T00:02:00Z",
        kind,
        lane: null,
        subject: null,
      });
      r = await revision(2, value, 9999999);
      await store.poll();
      expect(store.status().kind).toBe("stale");
    },
  );
  it("keeps a failed newer pointer explicit instead of exempting an old terminal cut", async () => {
    const value = fixture();
    value.document.journal.push({
      id: 2,
      at: "2026-11-15T00:01:00Z",
      kind: "delivery.closed",
      lane: null,
      subject: null,
    });
    let r = await revision(1, value);
    let missing = false;
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : missing
            ? new Response("", { status: 404 })
            : new Response(r.bytes, {
                headers: { "Content-Type": "application/json" },
              }),
    });
    await store.poll();
    const before = store.projection();
    expect(store.status().kind).toBe("retained");
    value.document.journal.push({
      id: 3,
      at: "2026-11-15T00:02:00Z",
      kind: "check.started",
      lane: null,
      subject: null,
    });
    r = await revision(2, value);
    missing = true;
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("unavailable");
    expect(store.status().receipt?.sequence).toBe(1);
  });
  it("rejects a changed digest at the same accepted epoch and sequence", async () => {
    let r = await revision();
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    const before = store.projection(),
      receipt = store.status().receipt;
    const changed = fixture();
    changed.document.state.meta = { headline: "Conflicting immutable tuple" };
    r = await revision(1, changed);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().kind).toBe("incompatible");
    expect(store.status().receipt).toEqual(receipt);
  });
  it("does not publish a completed late request after the observer is stopped", async () => {
    const r = await revision();
    let release!: () => void, entered!: () => void;
    const pending = new Promise<void>((resolve) => (release = resolve)),
      hashing = new Promise<void>((resolve) => (entered = resolve));
    const original = crypto.subtle.digest.bind(crypto.subtle);
    const spy = vi
      .spyOn(crypto.subtle, "digest")
      .mockImplementationOnce(async (...args) => {
        const value = await original(...args);
        entered();
        await pending;
        return value;
      });
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    const pushed = vi.fn(),
      statuses = vi.fn();
    store.subscribe(pushed);
    store.onHostingStatus(statuses);
    const loading = store.poll();
    try {
      await hashing;
      store.stop();
      release();
      await loading;
      expect(pushed).not.toHaveBeenCalled();
      expect(statuses).toHaveBeenCalledTimes(1);
      expect(store.read()).toBeNull();
      expect(store.status().receipt).toBeNull();
      store.start();
      await store.poll();
      expect(pushed).toHaveBeenCalledTimes(1);
      expect(store.read()).toEqual(r.body.document);
      expect(store.status().receipt).toMatchObject({ epoch: 1, sequence: 1 });
    } finally {
      release();
      await loading;
      store.stop();
      spy.mockRestore();
    }
  });
});

describe("epoch-aware reader recovery", () => {
  it("accepts the next epoch with a reset sequence and refuses a delayed lower epoch", async () => {
    let r = await revision(10);
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    const next = fixture();
    next.document.state.meta = { headline: "Recovered epoch two" };
    r = await revision(1, next);
    r.meta.epoch = 2;
    await store.poll();
    expect(store.read()).toEqual(next.document);
    expect(store.status().receipt).toMatchObject({ epoch: 2, sequence: 1 });
    expect(store.status().kind).toBe("current");
    const before = store.projection();
    r = await revision(11);
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(store.status().receipt).toMatchObject({ epoch: 2, sequence: 1 });
    expect(store.status().kind).toBe("unavailable");
  });
});

describe("immutable tuple confirmations", () => {
  it("accepts a newer source confirmation on the exact revision without replacing its document", async () => {
    const r = await revision();
    const store = createViewStore(publicId, {
      fetcher: async (url) =>
        url.endsWith("/current")
          ? Response.json(r.meta)
          : new Response(r.bytes, {
              headers: { "Content-Type": "application/json" },
            }),
    });
    await store.poll();
    const before = store.projection();
    const pushed = vi.fn();
    store.subscribe(pushed);
    r.meta.sourceConfirmedAt += 100;
    r.meta.serverNow += 100;
    await store.poll();
    expect(store.projection()).toBe(before);
    expect(pushed).not.toHaveBeenCalled();
    expect(store.status().kind).toBe("current");
    expect(store.status().receipt).toMatchObject({
      epoch: 1,
      sequence: 1,
      digest: r.meta.digest,
      sourceConfirmedAt: 1000100,
      serverNow: 1000100,
    });
  });
});

/**
 * A delivery underway: one piece has landed. Only then does the line map draw its live line under the hero; a fresh
 * delivery opens on what is about to land, and the hosting status is said on its own.
 */
function underway(body: PublicProjection): PublicProjection {
  (body.document.state.queue as Record<string, unknown>[]).unshift({
    subject: "APP-0",
    title: "Landed first",
    lane: "one",
    stage: "merged",
    head: "c".repeat(40),
  });
  return body;
}

describe("the hosting status in the hero's live line", () => {
  type Built = { renderer: string; viewer: string; shell: string };
  const built = () =>
    JSON.parse(
      readFileSync(new URL("./assets.generated.json", import.meta.url), "utf8"),
    ) as Built;
  type Served = Awaited<ReturnType<typeof revision>> | Error;
  /** The viewer and, when `draw`, the renderer on one page, served whatever `served.now` holds at each request. */
  async function hostedPage(served: { now: Served }, draw = true) {
    const assets = built();
    const dom = testDOM();
    Object.assign(dom.window, {
      fetch: async (url: string) => {
        const now = served.now;
        if (now instanceof Error) throw now;
        return url.endsWith("/current")
          ? Response.json(now.meta)
          : new Response(now.bytes, {
              headers: { "Content-Type": "application/json" },
            });
      },
    });
    dom.window.eval(assets.viewer);
    const document = dom.window.document;
    // The viewer hands its store to the renderer once it holds data.
    if (draw)
      await vi.waitFor(() =>
        expect(Reflect.get(dom.window, "deliveryStateStore")).toBeDefined(),
      );
    const store = Reflect.get(dom.window, "deliveryStateStore") as ReturnType<
      typeof createViewStore
    >;
    if (draw) {
      dom.window.eval(assets.renderer);
      await vi.waitFor(() =>
        expect(document.querySelector(".fact.since[data-hosting]")).not.toBeNull(),
      );
    }
    return { dom, store, document };
  }
  const line = (document: Document) =>
    document.querySelector<HTMLElement>(".fact.since")!;
  const status = (document: Document) =>
    document.getElementById("hosting-status")!;
  function close(dom: JSDOM) {
    dom.window.dispatchEvent(new dom.window.Event("pagehide"));
    dom.window.close();
  }
  it("draws no strip, and a current publication shows only as the live line, its times on hover or focus", async () => {
    expect(built().shell).not.toMatch(/hosting-strip|hosting-times|<aside/);
    const served = { now: (await revision(1, underway(auxiliaryProjection("old")))) as Served };
    const { dom, document } = await hostedPage(served, false);
    try {
      await vi.waitFor(() =>
        expect(Reflect.get(dom.window, "deliveryStateStore")).toBeDefined(),
      );
      const store = Reflect.get(dom.window, "deliveryStateStore") as ReturnType<
        typeof createViewStore
      >;
      // Before the line is drawn the status is said on its own; drawing the line alone, with no status change between
      // (the store's own tick is a second away), puts it out of sight.
      expect(status(document).classList.contains("hosted-announce")).toBe(false);
      dom.window.eval(built().renderer);
      expect(document.querySelector(".fact.since[data-hosting]")).not.toBeNull();
      await Promise.resolve();
      expect(status(document).classList.contains("hosted-announce")).toBe(true);
      await store.poll();
      expect(store.status().kind).toBe("current");
      const fact = line(document);
      expect(fact.dataset.hosting).toBe("current");
      expect(fact.querySelector(".hosted-words")).toBeNull();
      const handle = fact.querySelector<HTMLElement>('[data-tab="hosting"]')!;
      expect(handle.textContent).toBe("Live");
      expect(handle.getAttribute("tabindex")).toBe("0");
      expect(handle.getAttribute("aria-describedby")).toBe("hosting-times");
      const tip = document.getElementById("hosting-times")!;
      expect(tip.getAttribute("role")).toBe("tooltip");
      expect(tip.textContent).toMatch(/^Source confirmed .+ · Published .+ · Revision 1\.1$/);
      expect(tip.previousElementSibling).toBe(handle);
      expect(status(document).textContent).toBe("Progress confirmed");
      expect(status(document).getAttribute("aria-live")).toBe("polite");
      expect(status(document).classList.contains("hosted-announce")).toBe(true);
      expect(document.querySelectorAll(".hosting-strip, aside, #hosting-times")).toHaveLength(1);
    } finally {
      close(dom);
    }
  });
  it.each([
    ["stale", "Progress is stale · publisher contact lost"],
    ["unavailable", "Update unavailable · showing the last saved progress"],
    ["incompatible", "Incompatible update · showing the last saved progress"],
  ])("says a %s publication in the live line without interaction, and announces it", async (kind, words) => {
    const body = underway(auxiliaryProjection("old"));
    const served = { now: (await revision(1, body)) as Served };
    const { dom, store, document } = await hostedPage(served);
    try {
      await store.poll();
      const handle = line(document).querySelector<HTMLElement>('[data-tab="hosting"]')!;
      handle.focus();
      expect(document.activeElement).toBe(handle);
      if (kind === "stale") served.now = await revision(2, body, 1120000);
      else if (kind === "unavailable") served.now = Error("offline");
      else {
        const next = await revision(2, body);
        next.meta.rendererVersion = "delivery-line-map/0";
        served.now = next;
      }
      await store.poll();
      expect(store.status().kind).toBe(kind);
      const fact = line(document);
      await vi.waitFor(() => expect(fact.isConnected && fact.dataset.hosting).toBe(kind));
      const said = fact.querySelector(".hosted-words")!;
      expect(said.textContent).toBe(words);
      expect(said.getAttribute("aria-hidden")).toBe("true");
      expect(status(document).textContent).toBe(words);
      expect(status(document).dataset.state).toBe(kind);
      expect(status(document).getAttribute("role")).toBe("status");
      expect(document.getElementById("hosting-times")!.textContent).toContain(
        kind === "stale" ? "Revision 1.2" : "Revision 1.1",
      );
      // The renderer redraws the line as the connection drops; keyboard focus comes back to its label.
      expect((document.activeElement as HTMLElement).dataset.tab).toBe("hosting");
      expect(document.activeElement!.isConnected).toBe(true);
      // Back to current: the words go, and the line is quiet again.
      served.now = await revision(3, body);
      await store.poll();
      await vi.waitFor(() => expect(line(document).dataset.hosting).toBe("current"));
      expect(line(document).querySelector(".hosted-words")).toBeNull();
    } finally {
      close(dom);
    }
  });
  it("keeps a completed delivery's saved and retained publication quiet in its closed line until its connection fails", async () => {
    const body = auxiliaryProjection("old");
    body.document.journal.push({
      id: body.document.journal.length + 1,
      at: "2026-11-15T00:01:00Z",
      kind: "delivery.closed",
      lane: null,
      subject: null,
    });
    const served = { now: (await revision(1, body, 9999999)) as Served };
    const { dom, store, document } = await hostedPage(served);
    try {
      await store.poll();
      expect(store.status().kind).toBe("retained");
      await vi.waitFor(() => expect(line(document).dataset.hosting).toBe("retained"));
      expect(line(document).classList.contains("closed")).toBe(true);
      expect(line(document).querySelector(".hosted-words")).toBeNull();
      expect(document.getElementById("hosting-times")!.textContent).toContain("Revision 1.1");
      expect(status(document).textContent).toBe("Completed delivery · saved and retained");
      // A lost connection is said in the line, though the saved delivery stays readable.
      served.now = Error("offline");
      await store.poll();
      expect(store.status().kind).toBe("retained");
      await vi.waitFor(() =>
        expect(line(document).querySelector(".hosted-words")?.textContent).toBe(
          "Saved completed delivery · connection unavailable",
        ),
      );
      expect(status(document).textContent).toBe("Saved completed delivery · connection unavailable");
    } finally {
      close(dom);
    }
  });
  it("says the status on its own while a fresh delivery draws no live line", async () => {
    const served = { now: (await revision(1, auxiliaryProjection("old"))) as Served };
    const { dom, document } = await hostedPage(served, false);
    try {
      await vi.waitFor(() =>
        expect(Reflect.get(dom.window, "deliveryStateStore")).toBeDefined(),
      );
      const store = Reflect.get(dom.window, "deliveryStateStore") as ReturnType<
        typeof createViewStore
      >;
      dom.window.eval(built().renderer);
      await store.poll();
      expect(store.status().kind).toBe("current");
      // Nothing has landed: the page looks ahead, with no live line under the hero.
      expect(document.querySelector(".is-fresh")).not.toBeNull();
      expect(document.querySelector(".fact.since")).toBeNull();
      expect(status(document).textContent).toBe("Progress confirmed");
      expect(status(document).classList.contains("hosted-announce")).toBe(false);
    } finally {
      close(dom);
    }
  });
  it("says the status on its own, in the page's column, only before any live line is drawn", async () => {
    const served = { now: Error("offline") as Served };
    const { dom, store, document } = await hostedPage(served, false);
    try {
      // While loading, the page's own loading words say it; the region only announces.
      expect(status(document).textContent).toBe("Loading hosted progress…");
      expect(status(document).classList.contains("hosted-announce")).toBe(true);
      expect(store).toBeUndefined();
      await vi.waitFor(() =>
        expect(status(document).textContent).toBe("Hosted progress unavailable"),
      );
      expect(status(document).dataset.state).toBe("unavailable");
      expect(status(document).classList.contains("hosted-announce")).toBe(false);
      expect(document.querySelector(".fact.since")).toBeNull();
    } finally {
      close(dom);
    }
  });
});
