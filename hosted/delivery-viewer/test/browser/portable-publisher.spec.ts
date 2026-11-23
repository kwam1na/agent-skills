// The real publisher against the portable server: the corpus Publisher and Transport, through the Transport's
// loopback test seam, acquire, commit twice, confirm, read status, retry exactly, survive a restart of the server,
// and the page renders the last committed cut. Source and fixture proof; installed-adopter proof stays live.
import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { credentials, startNode, target } from "../launcher.mjs";

test.skip(target !== "node", "the real publisher runs against the portable server (playwright.node.config.ts)");

const driver = fileURLToPath(new URL("../node/publish.py", import.meta.url));
const [{ repository, token }] = credentials;
function publish(state: string, origin: string, command: string, title?: string) {
  const run = spawnSync("python3", ["-B", driver, command, state, origin, repository, ...(title ? [title] : [])],
    { encoding: "utf8", env: { PATH: process.env.PATH, HOSTED_PUBLISH_TOKEN: token } as unknown as NodeJS.ProcessEnv, timeout: 60_000 });
  expect(run.status, run.stderr).toBe(0);
  return JSON.parse(run.stdout);
}

test("the corpus publisher commits, confirms, retries and survives a restart; the page shows the last cut", async ({ page }) => {
  const folder = mkdtempSync(join(tmpdir(), "hosted-real-publisher-"));
  const dataDir = join(folder, "data"), state = join(folder, "publisher");
  mkdirSync(dataDir);
  let server = await startNode({ dataDir });
  try {
    const first = publish(state, server.origin, "publish", "First portable cut");
    expect(first.result).toMatchObject({ state: "current", receipt: { epoch: 1, sequence: 1 } });
    const second = publish(state, server.origin, "publish", "Second portable cut");
    expect(second.result).toMatchObject({ state: "current", receipt: { epoch: 1, sequence: 2 } });
    const confirmed = publish(state, server.origin, "publish", "Second portable cut");
    expect(confirmed.result.receipt).toEqual(second.result.receipt);
    expect(confirmed.result.sourceConfirmedAt).toBeGreaterThanOrEqual(second.result.receipt.sourceConfirmedAt);
    expect(publish(state, server.origin, "status").status).toMatchObject({ epoch: 1, sequence: 2, receipt: second.result.receipt });
    const retried = publish(state, server.origin, "retry", "Second portable cut");
    expect(retried.receipt).toEqual(retried.acknowledged);
    expect(retried.receipt).toEqual(second.result.receipt);

    expect(await server.stop("SIGTERM")).toEqual({ code: 0, signal: null });
    server = await startNode({ dataDir });
    const after = publish(state, server.origin, "publish", "Second portable cut");
    expect(after.result.receipt).toEqual(second.result.receipt);
    expect(after.publicId).toBe(first.publicId);

    await page.goto(`${server.origin}/d/${first.publicId}`);
    await expect(page.locator("#app")).toContainText("Second portable cut");
    await expect(page.locator("#app")).not.toContainText("First portable cut");
    await expect(page.locator("#hosting-status")).toContainText("Progress confirmed");
  } finally {
    await server.stop("SIGTERM");
    rmSync(folder, { recursive: true, force: true });
  }
});
