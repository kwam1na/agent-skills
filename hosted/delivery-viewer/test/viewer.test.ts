import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { describe, it, expect, vi } from "vitest";
import { createViewStore, type PublicProjection } from "../src/view-store";
import { cases } from "./fixtures.generated";
import { digest, RENDERER } from "../src/protocol";
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
      summary: "Completed",
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
      summary: "Reopened",
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
      expect(window.document.querySelector("#plan")).not.toBeNull();
      const detail = window.document.querySelector("#detail")!.textContent;
      expect(detail).toContain("APP-1");
      const fold = Array.from(window.document.querySelectorAll("summary")).find(
        (row) => row.textContent?.includes("All units"),
      )!.parentElement as HTMLDetailsElement;
      fold.open = true;
      fold.dispatchEvent(new window.Event("toggle"));
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
