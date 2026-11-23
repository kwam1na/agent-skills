// hosted.conformance-node: the conformance suite's fast tier against a portable server built from a service bundle.
//   node test/conformance/bundle.mjs
// It builds the bundle from this checkout with the release's own builder (scripts/cut-release.py --service-bundle),
// extracts it under TMPDIR, outside any git worktree, so no checkout's node_modules or repository root can supply a
// missing input, then installs, builds and runs the server and the suite from that extraction alone.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export const CHECKOUT = fileURLToPath(new URL("../../../../", import.meta.url));
const VERSION = "v0.0.0";

function run(command, args, options = {}) {
  const done = spawnSync(command, args, { stdio: "inherit", ...options });
  if (done.error || done.status !== 0)
    throw new Error(`${command} ${args.join(" ")} failed (${done.error?.code ?? done.status ?? done.signal})`);
  return done;
}

/** Builds the service bundle from `checkout`, checks its digest and extracts it under TMPDIR. */
export function extractBundle(checkout = CHECKOUT) {
  const work = mkdtempSync(join(tmpdir(), "hosted-bundle-"));
  try {
    const out = join(work, "out"), root = join(work, "extracted");
    run("python3", ["-B", join(checkout, "scripts/cut-release.py"), "--service-bundle", "--root", checkout, "--version", VERSION, "--out", out]);
    // The builder writes exactly the bundle and its .sha256, both or neither.
    const written = readdirSync(out).sort();
    if (written.length !== 2 || !written[0].endsWith(".zip") || written[1] !== `${written[0]}.sha256`)
      throw new Error(`the builder wrote ${written.join(", ") || "nothing"}, not a bundle and its .sha256`);
    const zip = join(out, written[0]);
    const recorded = readFileSync(`${zip}.sha256`, "utf8").split(/\s+/)[0];
    const actual = createHash("sha256").update(readFileSync(zip)).digest("hex");
    if (recorded !== actual) throw new Error(`the bundle's digest is ${actual}, its .sha256 says ${recorded}`);
    run("python3", ["-I", "-m", "zipfile", "-e", zip, root]);
    const inside = spawnSync("git", ["-C", root, "rev-parse", "--is-inside-work-tree"], { encoding: "utf8" });
    if (inside.status === 0) throw new Error(`the extraction ${root} sits inside a git worktree`);
    return { root, zip, digest: actual, cleanup: () => rmSync(work, { recursive: true, force: true }) };
  } catch (error) {
    rmSync(work, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.length > 2) {
    console.error("usage: bundle.mjs");
    process.exit(2);
  }
  let bundle;
  try {
    bundle = extractBundle();
    console.log(`extracted the service bundle (sha256 ${bundle.digest}) to ${bundle.root}`);
    const service = join(bundle.root, "hosted/delivery-viewer");
    run("npm", ["ci", "--ignore-scripts", "--no-audit", "--no-fund", "--engine-strict"], { cwd: service });
    run("npm", ["run", "build:node"], { cwd: service });
    run(process.execPath, ["test/conformance/check-node.mjs"], { cwd: service });
    console.log("\nhosted.conformance-node passed: the portable server built from the extracted bundle passes the fast tier");
  } catch (error) {
    console.error(`\nhosted.conformance-node failed: ${error.message}`);
    process.exitCode = 1;
  } finally {
    bundle?.cleanup();
  }
}
