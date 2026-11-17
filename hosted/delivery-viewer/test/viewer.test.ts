import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
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
    let mode = "good";
    const store = createViewStore(publicId, {
      fetcher: async (url) => {
        if (mode === "network") throw Error("private-url-canary");
        if (url.endsWith("/current"))
          return Response.json(
            mode === "renderer"
              ? { ...next.meta, rendererVersion: "future" }
              : mode === "good"
                ? good.meta
                : mode === "schema"
                  ? future.meta
                  : next.meta,
          );
        if (mode === "404") return new Response("", { status: 404 });
        if (mode === "digest") return Response.json(good.body);
        if (mode === "schema") return Response.json(future.body);
        return Response.json(mode === "good" ? good.body : next.body);
      },
    });
    await store.poll();
    const before = store.projection();
    for (const failure of ["network", "renderer", "404", "digest", "schema"]) {
      mode = failure;
      await store.poll();
      expect(store.projection()).toBe(before);
      expect(store.status().kind).toMatch(/unavailable|incompatible/);
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
      // The snapshot carries no story, so its chapter says that plainly, never that the story could not be told.
      expect(window.document.querySelector("#app")!.textContent).toContain(
        "What happened is not published on the hosted page.",
      );
      expect(window.document.querySelector("#app")!.textContent).not.toContain(
        "could not be told",
      );
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
      journalId: 2,
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
    '<!doctype html><body><p id="hosting-status"></p><p id="hosting-times"></p><main id="app"></main><script data-renderer="/assets/renderer.js"></script></body>',
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
