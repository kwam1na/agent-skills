import { test, expect } from "@playwright/test";
import { Miniflare, Log, LogLevel } from "miniflare";
import { readFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";

const token = "synthetic-browser-publisher-".padEnd(64, "x");
const hash = (value: string) =>
  createHash("sha256").update(value).digest("hex");
const fixture = (name = "valid-dissent-markup.json") =>
  JSON.parse(
    readFileSync(
      new URL(
        `../../../../tests/fixtures/delivery-views/hosted/${name}`,
        import.meta.url,
      ),
      "utf8",
    ),
  );
let runtime: Miniflare, origin: string;
test.beforeAll(async () => {
  runtime = new Miniflare({
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
            modules: {
              "worker.js": {
                type: "esm",
                contents: readFileSync(
                  new URL("../../dist/worker.js", import.meta.url),
                  "utf8",
                ),
              },
            },
          },
          exports: {
            DeliveryObject: { type: "durable-object", storage: "sqlite" },
          },
          env: {
            PUBLISHERS: {
              type: "text",
              value: JSON.stringify({ "browser-repo": hash(token) }),
            },
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
  origin = (await runtime.ready).origin;
});
test.afterAll(async () => {
  await runtime?.dispose();
});
const route = (id: string, action: string) =>
  `${origin}/api/v1/repos/browser-repo/deliveries/${id}/${action}`;
async function publish(id: string, value: unknown, sequence = 1) {
  const body = JSON.stringify(value);
  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  if (sequence === 1) {
    const result = await fetch(route(id, "session"), {
      method: "POST",
      headers,
      body: JSON.stringify({
        mode: "acquire",
        deliveryKey: "APP-1",
        ownerId: randomBytes(32).toString("hex"),
      }),
    });
    expect(result.status).toBe(200);
  }
  const result = await fetch(route(id, "snapshots"), {
    method: "POST",
    headers: {
      ...headers,
      "X-Writer-Epoch": "1",
      "X-Snapshot-Sequence": String(sequence),
      "X-Snapshot-Digest": hash(body),
      "X-Renderer-Version": "delivery-line-map/1",
    },
    body,
  });
  expect(result.status).toBe(200);
  return result.json() as Promise<{ digest: string }>;
}

test("signed-out data-only view enforces CSP, headers, no-referrer and no opener", async ({
  page,
  context,
}, info) => {
  const id = randomBytes(32).toString("hex");
  const body = fixture("valid-evidence-markers.json");
  body.view.runs = true;
  body.document.state.queue[0].title =
    'Hostile <img src=x onerror="window.__injected=1"> <script>window.__injected=2</script>';
  body.links.issues = { "APP-1": "https://example.com/issue/APP-1" };
  body.links.commits = { ["a".repeat(40)]: `https://example.com/${id}` };
  const accepted = await publish(id, body);
  const external: { referer: string | undefined }[] = [];
  await context.route("https://example.com/**", async (route) => {
    external.push({ referer: (await route.request().allHeaders()).referer });
    await route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>External issue</title><p>External</p>",
    });
  });
  const requests: { path: string; auth: string | undefined }[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith(origin))
      requests.push({
        path: new URL(request.url()).pathname,
        auth: request.headers().authorization,
      });
  });
  const response = await page.goto(`${origin}/d/${id}`);
  expect(response?.status()).toBe(200);
  const headers = response!.headers();
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["cache-control"]).toBe("no-store");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-robots-tag"]).toBe("noindex, nofollow, noarchive");
  expect(headers["content-security-policy"]).toContain("script-src 'self'");
  expect(headers["content-security-policy"]).not.toContain("unsafe-inline");
  expect(headers["content-security-policy"]).not.toContain("unsafe-eval");
  await expect(page.locator("#app")).toContainText("Hostile <img");
  await expect(page.locator("#hosting-status")).toContainText(
    "Progress confirmed",
  );
  expect(await page.locator("#app img,#app script").count()).toBe(0);
  await expect(page.locator("#detail")).toContainText("Changes requested");
  await expect(page.locator("#detail")).toContainText("<img onerror=x>");
  await expect(page.locator("#detail")).toContainText("shortened");
  await expect(page.locator("#detail")).toContainText("Brief unavailable");
  expect(
    await page
      .locator('a[href="https://example.com/issue/APP-1"]')
      .first()
      .getAttribute("rel"),
  ).toBe("noopener noreferrer");
  expect(
    await page.evaluate(() => Reflect.get(window, "__injected")),
  ).toBeUndefined();
  expect(await context.cookies()).toEqual([]);
  expect(requests.every((row) => row.auth === undefined)).toBe(true);
  expect(
    await page
      .locator('a[href*="example.com"]')
      .evaluateAll(
        (links, id) =>
          links.every(
            (link) => !link.getAttribute("href")!.includes(String(id)),
          ),
        id,
      ),
  ).toBe(true);
  expect(
    await page.getByRole("link", { name: "Past deliveries and runs" }).count(),
  ).toBe(0);
  for (const path of ["/", "/d", `/d/${id}/history`, `/d/${"0".repeat(64)}`])
    expect((await page.request.get(origin + path)).status()).toBe(404);
  expect(
    (
      await page.request.post(`${origin}/d/${id}`, {
        data: { action: "merge" },
      })
    ).status(),
  ).toBe(404);
  await page.evaluate(
    ({ origin, id, digest }) => {
      const script = document.createElement("script");
      script.textContent = "window.__inlineRan=1";
      document.head.appendChild(script);
      const button = document.createElement("button");
      button.setAttribute("onclick", "window.__handlerRan=1");
      document.body.appendChild(button);
      button.click();
      const data = document.createElement("script");
      data.src = `${origin}/d/${id}/snapshots/${digest}`;
      document.head.appendChild(data);
    },
    { origin, id, digest: accepted.digest },
  );
  await page.waitForTimeout(150);
  expect(
    await page.evaluate(() => [
      Reflect.get(window, "__inlineRan"),
      Reflect.get(window, "__handlerRan"),
      Reflect.get(window, "__injected"),
    ]),
  ).toEqual([undefined, undefined, undefined]);
  const foreignRequests: string[] = [];
  await context.route("https://example.com/__forbidden/**", async (route) => {
    foreignRequests.push(route.request().url());
    await route.fulfill({
      contentType: "text/javascript",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: "window.__foreignRan=1;",
    });
  });
  await page.evaluate(async () => {
    const scriptDenied = new Promise<void>((resolve) => {
      const script = document.createElement("script");
      script.src = "https://example.com/__forbidden/payload.js";
      script.onerror = () => resolve();
      script.onload = () => resolve();
      document.head.appendChild(script);
    });
    await Promise.all([
      scriptDenied,
      fetch("https://example.com/__forbidden/collect", {
        method: "POST",
        body: "synthetic fixture",
      }).catch(() => {}),
    ]);
  });
  expect(foreignRequests).toEqual([]);
  expect(
    await page.evaluate(() => Reflect.get(window, "__foreignRan")),
  ).toBeUndefined();
  const popupPromise = page.waitForEvent("popup");
  await page
    .locator('a[href="https://example.com/issue/APP-1"]')
    .first()
    .click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  expect(await popup.evaluate(() => window.opener)).toBeNull();
  expect(external).toEqual([{ referer: undefined }]);
  await popup.close();
  await expect(page.locator("#app")).toContainText("Hostile <img");
  await page.screenshot({
    path: info.outputPath("signed-out.png"),
    fullPage: true,
  });
});

test("polling preserves keyboard selection, folds, viewport and mobile line position", async ({
  page,
}, info) => {
  const id = randomBytes(32).toString("hex");
  const body = fixture();
  body.plan = fixture("valid-plan-review.json").plan;
  body.document.state.queue.push({
    subject: "APP-2",
    title: "Second unit",
    lane: "one",
    stage: "queued",
    head: "b".repeat(40),
  });
  await publish(id, body);
  await page.goto(`${origin}/d/${id}`);
  const second = page.locator('.stop-btn[data-subject="APP-2"]');
  await page.locator('.stop-btn[data-subject="APP-1"]').focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(second).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#detail")).toContainText("Second unit");
  await page.locator("#plan summary").filter({ hasText: "All units" }).click();
  await expect(
    page.locator("#plan details").filter({
      has: page.locator("summary").filter({ hasText: "All units" }),
    }),
  ).toHaveAttribute("open", "");
  await page.evaluate(() => {
    scrollTo(0, 500);
    const line = document.querySelector(".lines-scroll");
    if (line) line.scrollLeft = 100;
  });
  await page.waitForTimeout(50);
  const before = await page.evaluate(() => ({
    y: scrollY,
    line: document.querySelector(".lines-scroll")?.scrollLeft,
  }));
  body.document.state.meta.headline = "New acknowledged progress";
  body.plan.plan.outcome.summary = "Updated plan outcome";
  await publish(id, body, 2);
  await expect(page.locator("#app")).toContainText("New acknowledged progress");
  await expect(page.locator("#plan")).toContainText("Updated plan outcome");
  await expect(page.locator("#detail")).toContainText("Second unit");
  await expect(page.locator('.stop-btn[data-subject="APP-2"]')).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page.locator("#plan details").filter({
      has: page.locator("summary").filter({ hasText: "All units" }),
    }),
  ).toHaveAttribute("open", "");
  const after = await page.evaluate(() => ({
    y: scrollY,
    line: document.querySelector(".lines-scroll")?.scrollLeft,
    width: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }));
  expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(2);
  expect(Math.abs((after.line ?? 0) - (before.line ?? 0))).toBeLessThanOrEqual(
    2,
  );
  expect(after.width).toBeLessThanOrEqual(after.viewport);
  await page.locator('.stop-btn[data-subject="APP-1"]').focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#detail")).not.toContainText("Second unit");
  await page.screenshot({
    path: info.outputPath("updated-view.png"),
    fullPage: true,
  });
});

test("retains readable progress on pointer loss, incompatibility and publisher disconnection", async ({
  page,
}) => {
  const id = randomBytes(32).toString("hex");
  const body = fixture();
  body.document.state.meta.headline = "Saved progress";
  await publish(id, body);
  await page.goto(`${origin}/d/${id}`);
  await expect(page.locator("#app")).toContainText("Saved progress");
  body.document.state.meta.headline = "Recovered progress";
  const next = await publish(id, body, 2);
  await page.route(`**/d/${id}/snapshots/${next.digest}`, (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: '{"error":"not_found"}',
    }),
  );
  await expect(page.locator("#hosting-status")).toContainText(
    "Update unavailable",
  );
  await expect(page.locator("#app")).toContainText("Saved progress");
  await page.unroute(`**/d/${id}/snapshots/${next.digest}`);
  await expect(page.locator("#app")).toContainText("Recovered progress");
  await page.route(`**/d/${id}/current`, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    await route.fulfill({ json: { ...meta, rendererVersion: "future" } });
  });
  await expect(page.locator("#hosting-status")).toContainText(
    "Incompatible update",
  );
  await expect(page.locator("#app")).toContainText("Recovered progress");
  await page.unroute(`**/d/${id}/current`);
  await page.route(`**/d/${id}/current`, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    await route.fulfill({
      json: {
        ...meta,
        serverNow: meta.sourceConfirmedAt + 120000,
        stale: true,
      },
    });
  });
  await expect(page.locator("#hosting-status")).toContainText("stale");
  await expect(page.locator("#app")).toContainText("Recovered progress");
  body.document.journal.push({
    id: 2,
    at: "2026-11-15T00:01:00Z",
    kind: "delivery.closed",
    lane: "lead",
    subject: "APP-1",
    summary: "Delivery finished",
  });
  await publish(id, body, 3);
  await expect(page.locator("#hosting-status")).toContainText(
    "Completed delivery",
  );
  await expect(page.locator("#app")).toContainText("closed");
  await page.unroute(`**/d/${id}/current`);
  await page.route(`**/d/${id}/current`, (route) => route.abort());
  await page.waitForTimeout(5500);
  await expect(page.locator("#hosting-status")).toContainText(
    "Saved completed delivery",
  );
  await expect(page.locator("#app")).toContainText("closed");
});

test("asset failure stays explicit across later successful data polls", async ({
  page,
}) => {
  const id = randomBytes(32).toString("hex");
  await publish(id, fixture());
  const built = JSON.parse(
    readFileSync(new URL("../assets.generated.json", import.meta.url), "utf8"),
  );
  await page.route(`**${built.paths.rendererPath}`, (route) => route.abort());
  await page.goto(`${origin}/d/${id}`);
  await expect(page.locator("#hosting-status")).toContainText(
    "Viewer assets unavailable",
  );
  await page.waitForTimeout(5500);
  await expect(page.locator("#hosting-status")).toContainText(
    "Viewer assets unavailable",
  );
  await page.unroute(`**${built.paths.rendererPath}`);
  await page.reload();
  await expect(page.locator("#app")).toContainText("Delivery");
  await expect(page.locator("#hosting-status")).toContainText(
    "Progress confirmed",
  );
});

test("first render uses the newest coherent projection after a delayed renderer asset", async ({
  page,
}) => {
  const id = randomBytes(32).toString("hex");
  const body = fixture();
  body.view.runs = true;
  body.plan = fixture("valid-plan-review.json").plan;
  body.document.state.meta.headline = "Old startup document";
  body.plan.plan.outcome.summary = "Old startup plan";
  body.needs = {
    running: true,
    at: new Date().toISOString(),
    staleAfter: new Date(Date.now() + 600000).toISOString(),
    items: [
      {
        holding: true,
        source: "operator",
        text: "Old startup needs",
        clears: "Recorded resolution",
      },
    ],
  };
  body.links.issues = { "APP-1": "https://example.com/old-startup" };
  body.runs["run-1"].roundDetail[0].raised[0].summary =
    "Old startup run finding";
  await publish(id, body);
  const built = JSON.parse(
    readFileSync(new URL("../assets.generated.json", import.meta.url), "utf8"),
  );
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(`**${built.paths.rendererPath}`, async (route) => {
    await held;
    await route.continue();
  });
  try {
    await page.goto(`${origin}/d/${id}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#hosting-times")).toContainText("Revision 1.1");
    body.document.state.meta.headline = "Latest startup document";
    body.plan.plan.outcome.summary = "Latest startup plan";
    body.needs.items[0].text = "Latest startup needs";
    body.links.issues["APP-1"] = "https://example.com/latest-startup";
    body.runs["run-1"].roundDetail[0].raised[0].summary =
      "Latest startup run finding";
    await publish(id, body, 2);
    await expect(page.locator("#hosting-times")).toContainText("Revision 1.2");
    release();
    await expect(page.locator("#app")).toContainText("Latest startup document");
    await expect(page.locator("#plan")).toContainText("Latest startup plan");
    await expect(page.locator("#now")).toContainText("Latest startup needs");
    await expect(page.locator("#detail")).toContainText(
      "Latest startup run finding",
    );
    expect(
      await page.locator('a[data-ticket="APP-1"]').first().getAttribute("href"),
    ).toBe("https://example.com/latest-startup");
  } finally {
    release();
  }
});
