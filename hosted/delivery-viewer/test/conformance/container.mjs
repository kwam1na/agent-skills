// The container check: the image built from an extracted service bundle's Dockerfile, run read-only as its non-root
// user with a volume at /data, passes the conformance suite's fast tier.
//   node test/conformance/container.mjs
// It needs Docker. Where Docker is unavailable it says so and exits 3: unavailable, never passed. The image, the
// container and the volume are named for this run, and this run removes them.
import { spawnSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { extractBundle } from "./bundle.mjs";

const docker = (args, options = {}) => spawnSync("docker", args, { encoding: "utf8", ...options });
const probe = docker(["version", "--format", "{{.Server.Version}}"]);
if (probe.error || probe.status !== 0) {
  console.error("the container check is unavailable: Docker is not installed or not running here (not passed)");
  process.exit(3);
}
function must(args, what) {
  const done = docker(args);
  if (done.status !== 0) throw new Error(`${what} failed: ${(done.stderr || done.stdout).trim().slice(0, 400)}`);
  return done.stdout.trim();
}

const name = `hosted-delivery-check-${process.pid}-${Date.now()}`;
let bundle, container = null, volume = null, image = null;
try {
  bundle = extractBundle();
  must(["build", "-f", join(bundle.root, "hosted/delivery-viewer/Dockerfile"), "-t", `${name}:image`, bundle.root], "docker build");
  image = `${name}:image`;
  if (must(["image", "inspect", "--format", "{{.Config.User}}", image], "docker image inspect") !== "node")
    throw new Error("the image does not run as its non-root user");
  const provisioned = ["repo-container-a", "repo-container-b"].map((repository) => {
    const printed = must(["run", "--rm", "--read-only", image, "provision-token", repository], "provision-token");
    return { repository, token: /^token: (\S+)$/m.exec(printed)[1], entry: JSON.parse(/^PUBLISHERS entry: (\{.*\})$/m.exec(printed)[1]) };
  });
  volume = must(["volume", "create", `${name}-data`], "docker volume create");
  container = must(["run", "-d", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-v", `${volume}:/data`,
    "-e", `PUBLISHERS=${JSON.stringify(Object.assign({}, ...provisioned.map((row) => row.entry)))}`, "-p", "127.0.0.1::8080", image], "docker run");
  const origin = `http://${must(["port", container, "8080/tcp"], "docker port").split("\n")[0].replace("0.0.0.0", "127.0.0.1")}`;
  const deadline = Date.now() + 30_000;
  for (;;) {
    const answer = await fetch(`${origin}/`).then((response) => response.status, () => 0);
    if (answer === 404) break;
    if (Date.now() > deadline) throw new Error(`the container never answered at ${origin}: ${docker(["logs", container]).stderr.slice(0, 400)}`);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  const credentials = join(bundle.root, "..", "container-credentials.json");
  writeFileSync(credentials, JSON.stringify(provisioned.map(({ repository, token }) => ({ repository, token }))), { mode: 0o600 });
  const suite = spawnSync(process.execPath, [join(bundle.root, "hosted/conformance/cli.mjs"), "--origin", origin, "--credentials", credentials],
    { stdio: "inherit" });
  if (suite.status !== 0) throw new Error(`the conformance suite exited ${suite.status ?? suite.signal} against the container`);
  console.log("\nthe container check passed: the image from the extracted bundle passes the fast tier, read-only, as node");
} catch (error) {
  console.error(`\nthe container check failed: ${error.message}`);
  process.exitCode = 1;
} finally {
  if (container) docker(["rm", "-f", container]);
  if (volume) docker(["volume", "rm", "-f", volume]);
  if (image) docker(["rmi", "-f", image]);
  bundle?.cleanup();
}
