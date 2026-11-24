import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { credentials, startServer } from "../launcher.mjs";

// The runtime and its synthetic credentials come from the shared launcher, as the conformance run's do: the Worker,
// or the portable server under HOSTED_TARGET=node (playwright.node.config.ts).
const [{ repository, token }] = credentials;
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
/**
 * A delivery underway: one piece has landed. Only then does the line map draw its live line under the hero, where the
 * hosting status sits; a fresh delivery opens on what is about to land and says the status on its own.
 */
function underway<T extends { document: { state: { queue: unknown[] } } }>(body: T): T {
  body.document.state.queue.unshift({
    subject: "APP-0",
    title: "Landed first",
    lane: "one",
    stage: "merged",
    head: "c".repeat(40),
  });
  return body;
}
/**
 * Opens one chapter's page as a reader does with a chapter per page: from the corner's chapter tab, which a phone
 * shows too, where the side rail is hidden.
 */
async function openChapter(page: import("@playwright/test").Page, id: string) {
  await page.locator(".chapter-tab-button").click();
  await page.locator(`#chapter-tab-list a[href="#${id}"]`).click();
  await expect(page.locator(`section.chapter#${id}`)).toBeVisible();
}
let runtime: Awaited<ReturnType<typeof startServer>> | undefined, origin: string;
test.beforeAll(async () => {
  runtime = await startServer();
  origin = runtime.origin;
});
test.afterAll(async () => {
  await runtime?.dispose();
});
const route = (id: string, action: string) =>
  `${origin}/api/v1/repos/${repository}/deliveries/${id}/${action}`;
async function publish(id: string, value: unknown, sequence = 1, renderer = "delivery-line-map/3") {
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
      "X-Renderer-Version": renderer,
    },
    body,
  });
  expect(result.status).toBe(200);
  return result.json() as Promise<{ digest: string }>;
}

test("a v2 delivery keeps its signed-out page and deployed immutable assets", async ({ page }) => {
  const id = randomBytes(32).toString("hex");
  const snapshot = JSON.parse(readFileSync(new URL("../fixtures/snapshot-v2.json", import.meta.url), "utf8"));
  await publish(id, snapshot, 1, "delivery-line-map/2");
  const url = `${origin}/d/${id}`;
  const response = await page.goto(url);
  expect(response?.status()).toBe(200);
  // These exact asset URLs shipped on the original shared viewer.
  const deployed = [
    "/assets/0d13161f7ed59712f7a2245e4c95137bb05b21f26ec26c4dd016bc28e6b97787.js",
    "/assets/a3c505f2486b866ea70133ed39be10024ecd5084b33d88a08688f0c63cb15d80.js",
    "/assets/7475283f0ae841b7b61199da5c6d932ccac83818356d22f33bce51d758f2cc7d.css",
  ];
  const html = await response!.text();
  for (const path of deployed) expect(html).toContain(path);
  await expect(page.locator("#app")).toContainText("</script><b>Work</b>");
  await expect(page.locator("#hosting-status")).toContainText("Progress confirmed");
  snapshot.document.state.queue[0].title = "Second v2 publication";
  await publish(id, snapshot, 2, "delivery-line-map/2");
  await expect(page.locator("#app")).toContainText("Second v2 publication", { timeout: 25_000 });
  expect(page.url()).toBe(url);
});

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
  // The ticket opens from the line's page, where the stop names it.
  await openChapter(page, "line");
  const popupPromise = page.waitForEvent("popup");
  await page
    .locator('#line a[href="https://example.com/issue/APP-1"]')
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
  // The stops are on the line's page; the plan's folds on the plan's.
  await openChapter(page, "line");
  const second = page.locator('.stop-btn[data-subject="APP-2"]');
  await page.locator('.stop-btn[data-subject="APP-1"]').focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(second).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#detail")).toContainText("Second unit");
  await openChapter(page, "plan");
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
  await openChapter(page, "line");
  await page.locator('.stop-btn[data-subject="APP-1"]').focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#detail")).not.toContainText("Second unit");
  await page.screenshot({
    path: info.outputPath("updated-view.png"),
    fullPage: true,
  });
});

/** The weight CSS font matching picks for `wanted` among faces at `weights` (CSS Fonts 4, font-weight matching). */
function matchWeight(wanted: number, weights: number[]) {
  const all = [...new Set(weights)].sort((a, b) => a - b);
  if (all.includes(wanted)) return wanted;
  const below = all.filter((w) => w < wanted).reverse();
  const above = all.filter((w) => w > wanted);
  if (wanted >= 400 && wanted <= 500) {
    const near = above.filter((w) => w <= 500);
    return [...near, ...below, ...above.filter((w) => w > 500)][0];
  }
  return (wanted < 400 ? [...below, ...above] : [...above, ...below])[0];
}

test("renders in the local view's fonts from its own origin, with the hosting status in the live line", async ({
  page,
  context,
}) => {
  const id = randomBytes(32).toString("hex");
  await publish(id, underway(fixture()));
  const requested: string[] = [];
  context.on("request", (request) => requested.push(request.url()));
  const response = await page.goto(`${origin}/d/${id}`);
  // Today's policy, with font-src 'self' in place of font-src 'none'.
  expect(response!.headers()["content-security-policy"]).toBe(
    "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; connect-src 'self'; img-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  );
  await expect(page.locator(".fact.since[data-hosting='current']")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const loaded = await page.evaluate(() =>
    [...document.fonts]
      .filter((face) => face.status === "loaded")
      .map((face) => `${face.family.replace(/"/g, "")} ${face.style} ${face.weight}`),
  );
  for (const family of ["Newsreader", "Geist", "Geist Mono"])
    expect(loaded.some((face) => face.startsWith(`${family} normal `))).toBe(true);
  // What the local view's stylesheet asks for, and what the browser drew with.
  const cdp = await context.newCDPSession(page);
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  // For each family, the first heading or text the stylesheet sets in it, with words of its own on screen.
  for (const [within, family] of [
    ["h1", "Newsreader"],
    ["#app", "Geist"],
    ["#app", "Geist Mono"],
  ] as const) {
    // With a chapter per page, the line's page is where Now's neighbours set commit heads and stop codes in mono.
    if (family === "Geist Mono") await openChapter(page, "line");
    const computed = await page.evaluate(
      ([within, family]) => {
        const nodes = [...document.querySelectorAll(`${within}, ${within} *`)];
        const node = nodes.find((node) => {
          const style = getComputedStyle(node);
          const own = [...node.childNodes].some(
            (child) => child.nodeType === Node.TEXT_NODE && child.textContent!.trim(),
          );
          return (
            own &&
            node.getClientRects().length > 0 &&
            style.visibility === "visible" &&
            style.fontFamily.split(",")[0].replace(/"/g, "").trim() === family
          );
        });
        if (!node) return null;
        node.setAttribute("data-font-probe", family);
        const style = getComputedStyle(node);
        return { weight: style.fontWeight, style: style.fontStyle };
      },
      [within, family] as const,
    );
    expect(computed, `an element drawn in ${family}`).not.toBeNull();
    // The stylesheet is the local view's, so these are its values. Each is drawn from the face CSS font matching picks
    // among the weights the local link requests, as Google Fonts serves them: one face per weight.
    const served = await page.evaluate(
      ([family, style]) =>
        [...document.fonts]
          .filter((face) => face.family.replace(/"/g, "") === family && face.style === style)
          .map((face) => Number(face.weight)),
      [family, computed!.style] as const,
    );
    expect(loaded).toContain(
      `${family} ${computed!.style} ${matchWeight(Number(computed!.weight), served)}`,
    );
    const { root } = await cdp.send("DOM.getDocument", { depth: -1 });
    const { nodeId } = await cdp.send("DOM.querySelector", {
      nodeId: root.nodeId,
      selector: `[data-font-probe="${family}"]`,
    });
    const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
    expect(fonts.length).toBeGreaterThan(0);
    expect(fonts.every((font) => font.isCustomFont)).toBe(true);
    // A variable face reports its instance, as "Newsreader 16pt" for an optical size.
    expect(
      fonts.every((font) => font.familyName === family || font.familyName.startsWith(`${family} `)),
    ).toBe(true);
    if (family === "Geist")
      expect(fonts.some((font) => font.familyName.startsWith("Geist Mono"))).toBe(false);
  }
  // No strip: the live line carries the times, on hover and on focus. The live line is under Now's hero.
  await openChapter(page, "now");
  expect(await page.locator(".hosting-strip, aside").count()).toBe(0);
  await expect(page.locator("#hosting-status")).toHaveClass(/hosted-announce/);
  const handle = page.locator('.fact.since [data-tab="hosting"]');
  await expect(page.locator("#hosting-times")).toBeHidden();
  await handle.hover();
  await expect(page.locator("#hosting-times")).toBeVisible();
  await expect(page.locator("#hosting-times")).toContainText("Revision 1.1");
  await page.mouse.move(0, 0);
  await expect(page.locator("#hosting-times")).toBeHidden();
  await handle.focus();
  await expect(page.locator("#hosting-times")).toBeVisible();
  // Nothing left the hosted origin.
  expect(requested.length).toBeGreaterThan(4);
  expect(requested.filter((url) => !url.startsWith(origin + "/"))).toEqual([]);
  expect(requested.some((url) => url.endsWith(".woff2"))).toBe(true);
});

test("retains readable progress on pointer loss, incompatibility and publisher disconnection", async ({
  page,
}) => {
  const id = randomBytes(32).toString("hex");
  const body = underway(fixture());
  body.document.state.meta.headline = "Saved progress";
  await publish(id, body);
  await page.goto(`${origin}/d/${id}`);
  await expect(page.locator("#app")).toContainText("Saved progress");
  body.document.state.meta.headline = "Recovered progress";
  // Install the missing-snapshot response before publishing makes its digest visible to the polling browser.
  const snapshotRoute = `**/d/${id}/snapshots/${hash(JSON.stringify(body))}`;
  await page.route(snapshotRoute, (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: '{"error":"not_found"}',
    }),
  );
  await publish(id, body, 2);
  await expect(page.locator("#hosting-status")).toContainText(
    "Update unavailable",
  );
  // Said where a reader sees it without interaction: in the hero's live line.
  await expect(page.locator(".fact.since .hosted-words")).toBeVisible();
  await expect(page.locator(".fact.since .hosted-words")).toHaveText(
    "Update unavailable · showing the last saved progress",
  );
  await expect(page.locator("#app")).toContainText("Saved progress");
  await page.unroute(snapshotRoute);
  await expect(page.locator("#app")).toContainText("Recovered progress");
  await expect(page.locator(".fact.since .hosted-words")).toHaveCount(0);
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
  await expect(page.locator(".fact.since .hosted-words")).toBeVisible();
  await expect(page.locator(".fact.since .hosted-words")).toContainText(
    "Progress is stale",
  );
  await expect(page.locator("#app")).toContainText("Recovered progress");
  body.document.journal.push({
    id: 2,
    at: "2026-11-15T00:01:00Z",
    kind: "delivery.closed",
    lane: "lead",
    subject: "APP-1",
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
  await expect(page.locator(".fact.since .hosted-words")).toBeVisible();
  await expect(page.locator(".fact.since .hosted-words")).toHaveText(
    "Saved completed delivery · connection unavailable",
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
  // The page's own words say it; the region announces without repeating it on screen.
  await expect(page.locator("#app")).toHaveText(
    "Viewer assets unavailable. Reload this page to retry.",
  );
  await expect(page.locator("#hosting-status")).toHaveClass(/hosted-announce/);
  await page.waitForTimeout(5500);
  await expect(page.locator("#hosting-status")).toContainText(
    "Viewer assets unavailable",
  );
  await expect(page.locator("#hosting-status")).toHaveClass(/hosted-announce/);
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
  const body = underway(fixture());
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
    // The renderer is held, so no live line is drawn yet; the viewer still follows the revisions.
    const sequence = () =>
      page.evaluate(
        () =>
          (
            Reflect.get(window, "deliveryStateStore") as
              | { status(): { receipt: { sequence: number } | null } }
              | undefined
          )?.status().receipt?.sequence,
      );
    await expect.poll(sequence).toBe(1);
    body.document.state.meta.headline = "Latest startup document";
    body.plan.plan.outcome.summary = "Latest startup plan";
    body.needs.items[0].text = "Latest startup needs";
    body.links.issues["APP-1"] = "https://example.com/latest-startup";
    body.runs["run-1"].roundDetail[0].raised[0].summary =
      "Latest startup run finding";
    await publish(id, body, 2);
    await expect.poll(sequence).toBe(2);
    release();
    await expect(page.locator("#app")).toContainText("Latest startup document");
    await expect(page.locator("#hosting-times")).toContainText("Revision 1.2");
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
