// packages/cli/src/commands/check.ts
import path2 from "node:path";
import { deliveryRecordPathFor, resolveRecordStorage, BlockedError } from "./kernel.mjs";

// packages/cli/src/run-surface.ts
import { lstatSync } from "node:fs";
import path from "node:path";
import {
  createBlocker,
  createRunStore,
  sha256Hex,
  evaluateRunJournal,
  explainRunJournal,
  gitNamespaceClearedEnvironment,
  neutralizeForDisplay,
  resolveRunStoreLocation,
  runGitDirect
} from "./kernel.mjs";
var RUN_SURFACE_SOURCE = "delivery-harness.cli.run-surface";
var RUN_STORE_OVERRIDE = "DELIVERY_HARNESS_RUN_STORE";
function storeRootFor(commonDir, override) {
  const named = override?.trim() ?? "";
  if (named.length === 0) return { ok: true, root: commonDir };
  if (!path.isAbsolute(named)) {
    return { ok: false, reason: `${RUN_STORE_OVERRIDE} must name an absolute directory` };
  }
  return { ok: true, root: path.join(named, sha256Hex(commonDir)) };
}
async function resolveRunSurface(cwd) {
  const location = await resolveRunStoreLocation({
    cwd,
    run: runGitDirect,
    env: gitNamespaceClearedEnvironment()
  });
  if (!location.ok) return { ok: false, reason: location.reason };
  const rooted = storeRootFor(location.commonDir, process.env[RUN_STORE_OVERRIDE]);
  if (!rooted.ok) return { ok: false, reason: rooted.reason };
  const store = createRunStore(rooted.root);
  return {
    ok: true,
    surface: {
      store,
      commonDir: location.commonDir,
      runsDir: store.runsDir,
      worktreeKey: location.worktreeKey
    }
  };
}
async function resolveWorktreeRoot(cwd) {
  const outcome2 = await runGitDirect({
    cwd,
    args: ["rev-parse", "--path-format=absolute", "--show-toplevel"],
    env: gitNamespaceClearedEnvironment()
  });
  const root = outcome2.stdout.trim();
  if (outcome2.code !== 0 || root.length === 0) return { ok: false, reason: `not a git worktree: ${cwd}` };
  return { ok: true, root };
}
function runInstant() {
  return `${(/* @__PURE__ */ new Date()).toISOString().slice(0, 19)}Z`;
}
var MIRRORED = ["ticket", "candidateTreeSha"];
function buildRunEvent(input) {
  const payload = typeof input.payload === "object" && input.payload !== null ? input.payload : void 0;
  const mirrored = {};
  if (payload !== void 0) {
    for (const member of MIRRORED) {
      if (Object.prototype.hasOwnProperty.call(payload, member) && payload[member] !== void 0) {
        mirrored[member] = payload[member];
      }
    }
  }
  return {
    version: input.version ?? "run-event/1",
    ...input.eventId === void 0 ? {} : { eventId: input.eventId },
    runId: input.runId,
    at: runInstant(),
    repo: { commonDir: input.commonDir },
    kind: input.kind,
    actor: { role: input.role },
    ...mirrored,
    attestation: "self",
    payload: input.payload ?? null
  };
}
function oneLine(value2, maximum = 240) {
  const collapsed = neutralizeForDisplay(value2).replace(/\s+/g, " ").trim();
  return collapsed.length <= maximum ? collapsed : `${collapsed.slice(0, Math.max(maximum - 1, 0))}\u2026`;
}
function oneLineOf(value2, maximum = 240) {
  if (typeof value2 === "string") return oneLine(value2, maximum);
  if (value2 === void 0) return "";
  return oneLine(JSON.stringify(value2) ?? String(value2), maximum);
}
var RUN_JOURNAL_ROW_LABELS = "self-attested; observability, not evidence; bound to the record";
var ABSENT = { status: "absent", missing: [], attestation: "self" };
async function resolveRunJournalRow(input) {
  const resolved = await resolveRunSurface(input.cwd);
  if (!resolved.ok) return ABSENT;
  const acceptedTrees = [.../* @__PURE__ */ new Set([input.treeSha, ...input.reviewedCandidateTreeShas ?? []])];
  const matches = await Promise.all(acceptedTrees.map((treeSha) => resolved.surface.store.findByCandidateTreeSha(treeSha)));
  const match = matches.find((entry) => entry !== void 0);
  if (match === void 0) return ABSENT;
  const read = await resolved.surface.store.read(match.runId);
  if (!read.ok) return ABSENT;
  const evaluation = evaluateRunJournal(read.events, input.treeSha, input.mandatedLensIds, acceptedTrees.slice(1));
  const diagnostics = explainRunJournal(read.events, input.treeSha, input.mandatedLensIds, acceptedTrees.slice(1));
  const alsoMatching = [...new Set(matches.flatMap((entry) => entry === void 0 ? [] : [entry.runId, ...entry.alsoMatching]))].filter((runId) => runId !== match.runId);
  return {
    runId: match.runId,
    ...alsoMatching.length === 0 ? {} : { alsoMatching },
    status: evaluation.status,
    missing: evaluation.missing,
    ...evaluation.violations.length === 0 ? {} : { violations: evaluation.violations, explanations: diagnostics.explanations },
    ...acceptedTrees.length === 1 ? {} : { recordTreeSha: input.treeSha, reviewedCandidateTreeShas: acceptedTrees.slice(1) },
    ...diagnostics.roundBinding === void 0 ? {} : { roundBinding: diagnostics.roundBinding },
    attestation: "self"
  };
}
async function resolveRunSpan(input) {
  const resolved = await resolveRunSurface(input.cwd);
  if (!resolved.ok) return void 0;
  const matched = await resolved.surface.store.findByCandidateTreeSha(input.treeSha);
  const runIds = matched === void 0 ? [] : [matched.runId, ...matched.alsoMatching];
  if (runIds.length === 0 && input.allowCurrentRun === true) {
    const current = await resolved.surface.store.current(resolved.surface.worktreeKey);
    if (current.ok && current.runId !== void 0) runIds.push(current.runId);
  }
  let fallback;
  for (const runId of runIds) {
    const read = await resolved.surface.store.read(runId);
    if (!read.ok) continue;
    const startedAt = read.events[0]?.at;
    const endedAt = read.events[read.events.length - 1]?.at;
    if (startedAt === void 0 || endedAt === void 0) continue;
    const row = { runId, startedAt, endedAt };
    if (input.preferStartedAt === void 0 || startedAt === input.preferStartedAt) return row;
    fallback ??= row;
  }
  return fallback;
}
var ROUND_BINDING_ROWS = {
  "record-tree": "the governing closed round binds the record's own candidate tree",
  "reviewed-tree": "the governing closed round binds a reviewed candidate above, accepted from this record's verified review-neutral projection; the two trees differ and neither is claimed to equal the other",
  unbound: "no closed round binds the record's candidate tree or any reviewed candidate it accepts"
};
var RUN_JOURNAL_ADMISSION_ROW = "none of the above blocks admission: the record's own evidence decides that, and no gate, admission, or record decision reads a journal. Only --require-run-journal blocks, and only this verify invocation.";
function runJournalRows(row) {
  const rows = [`  run journal: ${oneLine(row.status, 64)}  (${RUN_JOURNAL_ROW_LABELS})`];
  if (row.runId !== void 0) rows.push(`    run: ${oneLine(row.runId, 128)}`);
  if (row.alsoMatching !== void 0 && row.alsoMatching.length > 0) {
    rows.push(`    also matching: ${row.alsoMatching.map((id) => oneLine(id, 128)).join(", ")}`);
  }
  if (row.recordTreeSha !== void 0 && row.reviewedCandidateTreeShas !== void 0) {
    rows.push(`    record candidate: ${oneLine(row.recordTreeSha, 64)}`);
    rows.push(`    reviewed candidate: ${row.reviewedCandidateTreeShas.map((tree) => oneLine(tree, 64)).join(", ")} (verified review-neutral projection)`);
  }
  if (row.roundBinding !== void 0) rows.push(`    round binding: ${ROUND_BINDING_ROWS[row.roundBinding]}`);
  rows.push(`    missing: ${row.missing.length === 0 ? "(none)" : row.missing.map((entry) => oneLine(entry, 64)).join(", ")}`);
  if (row.violations !== void 0 && row.violations.length > 0) {
    rows.push(`    violations: ${row.violations.map((entry) => oneLine(entry, 64)).join(", ")}`);
    for (const explanation of row.explanations ?? []) {
      const consequence = explanation.consequenceOf === void 0 ? "" : `; a consequence of ${oneLine(explanation.consequenceOf, 64)}, not a separate mistake`;
      rows.push(`      ${oneLine(explanation.violation, 64)}: ${oneLine(explanation.because, 400)}${consequence}`);
    }
    rows.push(`    admission: ${RUN_JOURNAL_ADMISSION_ROW}`);
  }
  return rows;
}
function runSurfaceBlocker(input) {
  return createBlocker({
    code: input.code,
    source: { kind: "command", id: RUN_SURFACE_SOURCE },
    summary: input.summary,
    ...input.details === void 0 ? {} : { details: input.details },
    remediations: [{ id: input.remediation.id, kind: "manual_action", summary: input.remediation.summary }]
  });
}
function harnessConfigPresentAt(rootDir) {
  try {
    return lstatSync(path.join(rootDir, "harness.config.ts")).isFile();
  } catch {
    return false;
  }
}

// packages/cli/src/commands/check.ts
var PROBE_FILE = ".delivery-harness-write-probe";
var checkCommand = {
  name: "check",
  sourceId: "delivery-harness.cli.check",
  summary: "Confirm the config loads and the evidence store is usable.",
  usage: "Usage: delivery-harness check\nTakes no arguments.",
  async run(context) {
    const unexpected = context.args[0];
    if (unexpected !== void 0) {
      return { kind: "usage", message: `check takes no arguments, and ${oneLine(unexpected, 64)} is one.
${checkCommand.usage}` };
    }
    try {
      const storage = await resolveRecordStorage(context.rootDir, { storageNamespace: context.config.storageNamespace });
      const probe = path2.join(storage.storageDir, PROBE_FILE);
      await context.artifacts.writeTextFile(probe, "probe\n", { mode: 384 });
      await context.artifacts.removeFile(probe);
      const recordPath = deliveryRecordPathFor(context.config, "<deliverableDigest>");
      const baseMovement = context.config.deliveryRecordVerification.baseMovement === "stale" ? "base movement stale: verification refuses base-ref, base-tip, or merge-base drift; admit again against the current base" : "base movement allow: verification may accept base drift and names every relaxed drift class";
      return {
        kind: "ok",
        summary: [
          `ok: gate ${context.config.gateId}`,
          `  ${context.config.obligations.length} obligation(s), ${context.config.providers.length} provider(s)`,
          `  store ${storage.storageDir} (writable)`,
          `  delivery record path ${recordPath}`,
          `  ${baseMovement}`
        ].join("\n")
      };
    } catch (error) {
      if (error instanceof BlockedError) {
        return { kind: "blocked", blockers: [...error.blockers] };
      }
      throw error;
    }
  }
};

// packages/cli/src/commands/admit.ts
import { readFile as readFile5 } from "node:fs/promises";
import path14 from "node:path";

// packages/cli/src/check-snapshot.ts
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readdir, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path3 from "node:path";
import { candidateTreeSourceReader, capturePhysicalSource, createExecPort, isPhysicalSourceDefinition, physicalSourceOutputOverlap } from "./kernel.mjs";

// packages/cli/src/snapshot-inventory-worker.ts
var snapshotInventoryWorker = String.raw`
import { lstat, readFile, readdir, readlink, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
const { root, outputs, excludeDependencies, forbidGit, includeSource, absentInputs = [], selfReferences = [] } = JSON.parse(process.env.DELIVERY_SNAPSHOT_REQUEST);
// A setup that writes this tree's own absolute path into a file binds the tree to where it is.
const references = selfReferences.map(reference => Buffer.from(reference));
let selfReferenced = false;
const entries = [];
const sourceEntries = [];
const record = (relative, tuple) => {
  entries.push(tuple);
  if (includeSource && !relative.split('/').includes('node_modules')) sourceEntries.push(tuple);
};
const inside = target => {
  const relative = path.relative(root, target);
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
const escape = () => { throw { code: 'check_snapshot_escape' }; };
// Eight workers bound filesystem operations, not just initial directory reads.
// Outstanding includes running directories: an empty queue is not completion
// while one of them may still discover children. Every started visit settles
// before an error is reported and the owner may clean up the private tree.
const drainQueue = async (queue, visit) => {
  let cursor = 0, outstanding = queue.length, failed = false, failure;
  const waiting = [];
  const wake = () => { for (const resume of waiting.splice(0)) resume(); };
  const worker = async () => {
    while (true) {
      while (!failed && cursor === queue.length && outstanding > 0) await new Promise(resolve => waiting.push(resolve));
      if (failed || outstanding === 0) return;
      const slot = queue[cursor++];
      try {
        const children = await visit(slot);
        if (!failed && children?.length) {
          outstanding += children.length;
          for (const child of children) queue.push(child);
        }
      } catch (error) { if (!failed) { failed = true; failure = error; } }
      finally { outstanding--; wake(); }
    }
  };
  await Promise.all(Array.from({ length: 8 }, worker));
  if (failed) throw failure;
};
const boundedMap = async (values, visit) => {
  const slots = values.map(value => ({ value }));
  await drainQueue([...slots], async slot => { slot.result = await visit(slot.value); });
  return slots.map(slot => slot.result);
};

// Existing directory coordinates come only from the actual walk, with exact
// bigint filesystem identity. Requested spelling and realpath are not canonical.
const directories = new Map();
const identity = stat => stat.dev.toString() + ':' + stat.ino.toString();
const resolveMissing = async target => {
  if (!inside(target)) escape();
  let resolved = root, key = root, links = 0;
  const pending = path.relative(root, target).split(path.sep);
  while (pending.length) {
    const segment = pending.shift();
    if (!segment || segment === '.') continue;
    if (segment === '..') { resolved = path.dirname(resolved); key = path.dirname(key); }
    else {
      const next = path.join(resolved, segment);
      const info = await lstat(next, { bigint: true }).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
      if (info?.isSymbolicLink()) {
        if (++links > 32) escape();
        const link = await readlink(next);
        if (path.isAbsolute(link)) escape();
        pending.unshift(...link.split(path.sep));
      } else if (info?.isDirectory()) {
        resolved = directories.get(identity(info));
        if (resolved === undefined) escape();
        key = resolved;
      } else {
        resolved = next;
        // No object exists to establish Unicode equivalence. The finite port
        // explicitly refuses non-ASCII missing suffixes rather than guessing a
        // filesystem folding table. Existing distinct directories stay distinct.
        if (info === null && /[^\x00-\x7f]/.test(segment)) throw { code: 'check_snapshot_drift' };
        key = info === null ? path.join(key, segment.toLowerCase()) : resolved;
      }
    }
    if (!inside(resolved)) escape();
  }
  return { absolute: resolved, key };
};
// Retain the already-walked links so output authority can follow directory
// referents without recursively following links or weakening inventory digests.
const aliases = [];
let protectedNames;
const protectOutputAlias = resolved => {
  const name = path.relative(root, resolved.key);
  for (const a of protectedNames) {
    if (name === '' || name === a || name.startsWith(a + '/') || a.startsWith(name + '/')) throw { code: 'check_snapshot_drift' };
  }
};
const protectOutputReachability = async () => {
  protectedNames = await boundedMap(absentInputs, async absent => path.relative(root, (await resolveMissing(path.join(root, absent))).key));
  const reachable = [];
  for (const output of outputs) reachable.push(await resolveMissing(path.join(root, output.replace(/\/$/, ''))));
  for (const target of reachable) protectOutputAlias(target);
  // Each collected link is expanded at most once. Its fully resolved target
  // is writable whenever its lexical location is below an output referent.
  const pending = new Set(aliases);
  for (let index = 0; index < reachable.length; index++) {
    const directory = reachable[index].absolute;
    for (const alias of pending) {
      const relative = path.relative(directory, alias.absolute);
      if (relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative)) continue;
      pending.delete(alias);
      const resolved = await resolveMissing(alias.absolute);
      protectOutputAlias(resolved);
      reachable.push(resolved);
    }
  }
};
const walk = async () => {
  const tree = { relative: '', mutable: false };
  await drainQueue([tree], async slot => {
    const { relative } = slot;
    if (relative === '.git' || (excludeDependencies && relative.split('/').includes('node_modules'))) return;
    // Mutable paths are excluded from the digest, never from the link/type walk.
    // Carry the exclusion through directories, including an exact output path.
    const mutable = slot.mutable || outputs.some(o => o.endsWith('/') ? relative === o.slice(0, -1) || relative.startsWith(o) : relative === o);
    const absolute = path.join(root, relative), stat = await lstat(absolute, { bigint: true });
    if (stat.isSymbolicLink()) {
      const target = await readlink(absolute);
      if (path.isAbsolute(target) || !inside(path.resolve(path.dirname(absolute), target))) escape();
      try { if (!inside(await realpath(absolute))) escape(); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (absentInputs.length) slot.alias = { absolute };
      if (!mutable) slot.tuple = [relative, 'link', target];
    } else if (stat.isDirectory()) {
      slot.directory = { identity: identity(stat), absolute };
      slot.children = (await readdir(absolute)).sort().map(name => ({ relative: relative ? relative + '/' + name : name, mutable }));
      return slot.children;
    } else if (stat.isFile()) {
      if (!mutable) {
        const bytes = await readFile(absolute);
        if (!selfReferenced && references.some(reference => bytes.includes(reference))) selfReferenced = true;
        slot.tuple = [relative, Number(stat.mode & 0o111n), createHash('sha256').update(bytes).digest('hex')];
      }
    } else escape();
  });
  // Completion order grants no directory authority and cannot change digest
  // bytes. Flatten exact sorted-child DFS, including a/child before a.c;
  // globally sorting relative paths would silently change the old identity.
  const ordered = [tree];
  while (ordered.length) {
    const slot = ordered.pop();
    if (slot.directory) directories.set(slot.directory.identity, slot.directory.absolute);
    if (slot.alias) aliases.push(slot.alias);
    if (slot.tuple) record(slot.relative, slot.tuple);
    if (slot.children) for (let index = slot.children.length - 1; index >= 0; index--) ordered.push(slot.children[index]);
  }
};
try {
  for (const absent of absentInputs) {
    let relative = '';
    for (const part of absent.split('/')) {
      relative = relative ? relative + '/' + part : part;
      const info = await lstat(path.join(root, relative)).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
      if (info === null) break;
      if (info.isSymbolicLink() || relative === absent || !info.isDirectory()) throw { code: 'check_snapshot_drift' };
    }
  }
  if (forbidGit && await lstat(path.join(root, '.git')).then(() => true, error => { if (error.code === 'ENOENT') return false; throw error; })) throw { code: 'check_snapshot_drift' };
  await walk();
  if (absentInputs.length) await protectOutputReachability();
  // Tuples contain only arrays, strings and permission integers: JSON.stringify
  // has exactly the same bytes as canonical JSON for this restricted shape.
  const digest = tuples => createHash('sha256').update(JSON.stringify(tuples)).digest('hex');
  process.stdout.write(JSON.stringify({ digest: digest(entries), ...(includeSource ? { sourceDigest: digest(sourceEntries) } : {}), ...(references.length ? { selfReferenced } : {}) }));
} catch (error) {
  process.stdout.write(JSON.stringify({ code: ['check_snapshot_escape', 'check_snapshot_drift'].includes(error.code) ? error.code : 'check_snapshot_unavailable' }));
  process.exitCode = 1;
}
`;

// packages/cli/src/check-snapshot.ts
var SNAPSHOT_TIMEOUT_MS = 5 * 6e4;
var CheckSnapshotError = class extends Error {
  code;
  constructor(code, message) {
    super(message);
    this.code = code;
  }
};
var nulSeparated = (text2) => text2.split("\0").filter(Boolean);
function withinOutputs(entry, outputs) {
  return outputs.some((output) => {
    const root = output.replace(/\/$/, "");
    return entry === root || entry.startsWith(`${root}/`);
  });
}
function phaseMeter(onPhase) {
  return async (phase, operation) => {
    const started = performance.now();
    try {
      return await operation();
    } finally {
      try {
        onPhase?.(phase, performance.now() - started);
      } catch {
      }
    }
  };
}
var trash = () => path3.join(tmpdir(), `delivery-harness-trash-${process.getuid?.() ?? "user"}`);
async function ownTrash(create) {
  const dir = trash();
  try {
    if (create) await mkdir(dir, { mode: 448 }).catch((error) => {
      if (error.code !== "EEXIST") throw error;
    });
    const found = await lstat(dir);
    return found.isDirectory() && found.uid === process.getuid?.() && (found.mode & 63) === 0 ? dir : void 0;
  } catch {
    return void 0;
  }
}
function deleteDetached(paths) {
  for (let at = 0; at < paths.length; at += 256) {
    try {
      const child = spawn("/bin/rm", ["-rf", "--", ...paths.slice(at, at + 256)], { detached: true, stdio: "ignore" });
      child.on("error", () => {
      });
      child.unref();
    } catch {
    }
  }
}
function ownerAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}
async function sweepRemovedTrees() {
  if (process.platform === "win32") return;
  const dir = await ownTrash(false);
  if (!dir) return;
  const entries = await readdir(dir).catch(() => []);
  deleteDetached(entries.filter((entry) => {
    const pid = Number(/^(\d+)-/.exec(entry)?.[1]);
    return !Number.isSafeInteger(pid) || pid <= 0 || !ownerAlive(pid);
  }).map((entry) => path3.join(dir, entry)));
}
async function removeTrees(directories) {
  const moved = [];
  const into = process.platform === "win32" ? void 0 : await ownTrash(true);
  const removed = await Promise.allSettled(directories.map(async (dir) => {
    if (into) {
      try {
        const [from, to] = await Promise.all([stat(path3.dirname(dir)), stat(into)]);
        if (from.dev === to.dev) {
          const target = path3.join(into, `${process.pid}-${randomUUID()}`);
          await rename(dir, target);
          moved.push(target);
          return;
        }
      } catch (error) {
        if (error.code === "ENOENT" && await lstat(dir).then(() => false, (gone) => gone.code === "ENOENT")) return;
      }
    }
    await rm(dir, { recursive: true, force: true });
  }));
  deleteDetached(moved);
  if (removed.some((result) => result.status === "rejected")) throw new CheckSnapshotError("check_snapshot_cleanup_failed", "Cannot remove the owned execution snapshot after retaining evidence.");
}
async function cloneTree(method, source, destination, deadline, signal) {
  const [command, args] = method === "clonefile" ? ["/usr/bin/perl", ["-e", "syscall(462, -2, $ARGV[0], -2, $ARGV[1], 0) == 0 or exit 1", source, destination]] : ["cp", ["-a", "--reflink=always", "--", source, destination]];
  const result = await snapshotProcess(command, [...args], path3.dirname(destination), { PATH: "/usr/bin:/bin" }, deadline, signal);
  if (result.code !== 0) throw new CheckSnapshotError("check_snapshot_unavailable", "The warm snapshot could not be cloned.");
}
var cloneProbe;
function snapshotCloneMethod() {
  return cloneProbe ??= (async () => {
    const method = process.platform === "darwin" ? "clonefile" : process.platform === "linux" ? "reflink" : void 0;
    if (!method) return void 0;
    const probe = await mkdtemp(path3.join(tmpdir(), "delivery-clone-probe-"));
    try {
      await mkdir(path3.join(probe, "source"));
      await writeFile(path3.join(probe, "source", "probe"), "probe");
      await cloneTree(method, path3.join(probe, "source"), path3.join(probe, "clone"), performance.now() + 3e4);
      return method;
    } catch {
      return void 0;
    } finally {
      await rm(probe, { recursive: true, force: true }).catch(() => void 0);
    }
  })();
}
async function cloneSnapshot(tree, method, signal, onPhase, open3) {
  return phaseMeter(onPhase)("clone", async () => {
    const parent = await realpath(await mkdtemp(path3.join(tmpdir(), "delivery-check-clone-")));
    const cloned = { rootDir: path3.join(parent, "tree"), ...tree.privateControl ? { privateControl: path3.join(parent, "control") } : {}, owned: [parent] };
    try {
      const deadline = performance.now() + SNAPSHOT_TIMEOUT_MS;
      await cloneTree(method, tree.rootDir, cloned.rootDir, deadline, signal);
      if (tree.privateControl) await cloneTree(method, tree.privateControl, cloned.privateControl, deadline, signal);
    } catch (error) {
      await rm(parent, { recursive: true, force: true }).catch(() => void 0);
      throw error instanceof CheckSnapshotError ? error : new CheckSnapshotError("check_snapshot_unavailable", "The warm snapshot could not be cloned.");
    }
    return open3(cloned, onPhase);
  });
}
function inside(root, target) {
  const relative = path3.relative(root, target);
  return relative !== ".." && !relative.startsWith(`..${path3.sep}`) && !path3.isAbsolute(relative);
}
async function snapshotProcess(command, args, cwd, env, deadline, signal, limit = 64 * 1024) {
  if (signal?.aborted) throw new CheckSnapshotError("check_snapshot_interrupted", "Snapshot verification was interrupted.");
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw new CheckSnapshotError("check_snapshot_timeout", "Snapshot verification exceeded its deadline.");
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    let failure2, stdout = "", size = 0;
    const stop = (code, message) => {
      failure2 ??= new CheckSnapshotError(code, message);
      child.kill("SIGKILL");
    };
    const abort = () => stop("check_snapshot_interrupted", "Snapshot verification was interrupted.");
    const timer = setTimeout(() => stop("check_snapshot_timeout", "Snapshot verification exceeded its deadline."), remaining);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    child.stdout.on("data", (bytes) => {
      size += bytes.length;
      if (size > limit) stop("check_snapshot_unavailable", "Snapshot verifier output exceeded its limit.");
      else stdout += bytes.toString();
    });
    child.stderr.on("data", () => {
    });
    child.on("error", () => {
      failure2 ??= new CheckSnapshotError("check_snapshot_unavailable", "Snapshot verifier could not execute.");
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (!failure2 && performance.now() >= deadline) failure2 = new CheckSnapshotError("check_snapshot_timeout", "Snapshot verification exceeded its deadline.");
      if (failure2) reject(failure2);
      else resolve({ code, stdout });
    });
  });
}
async function snapshotInventory(root, outputs, excludeDependencies = false, deadline = performance.now() + SNAPSHOT_TIMEOUT_MS, signal, forbidGit = false, absentInputs = []) {
  return (await snapshotDigests(root, outputs, excludeDependencies, deadline, signal, forbidGit, false, absentInputs)).digest;
}
async function snapshotDigests(root, outputs, excludeDependencies, deadline, signal, forbidGit = false, includeSource = false, absentInputs = [], selfReferences2 = []) {
  const result = await snapshotProcess(
    process.execPath,
    ["--input-type=module", "--eval", snapshotInventoryWorker],
    root,
    { PATH: process.env["PATH"] ?? "/usr/bin:/bin", DELIVERY_SNAPSHOT_REQUEST: JSON.stringify({ root, outputs, excludeDependencies, forbidGit, includeSource, absentInputs, selfReferences: selfReferences2 }) },
    deadline,
    signal
  );
  let parsed;
  try {
    parsed = JSON.parse(result.stdout);
  } catch {
    throw new CheckSnapshotError("check_snapshot_unavailable", "Snapshot inventory returned no valid result.");
  }
  if (result.code !== 0 || !/^[a-f0-9]{64}$/.test(parsed.digest ?? "") || includeSource && !/^[a-f0-9]{64}$/.test(parsed.sourceDigest ?? "")) throw new CheckSnapshotError(["check_snapshot_escape", "check_snapshot_drift"].includes(parsed.code ?? "") ? parsed.code : "check_snapshot_unavailable", "Snapshot inventory could not verify private bytes and links.");
  if (selfReferences2.length && typeof parsed.selfReferenced !== "boolean") throw new CheckSnapshotError("check_snapshot_unavailable", "Snapshot inventory returned no valid result.");
  return { digest: parsed.digest, ...includeSource ? { sourceDigest: parsed.sourceDigest } : {}, selfReferenced: parsed.selfReferenced === true };
}
function selfReferences(...created) {
  return [...new Set(created.flatMap((dir) => dir === void 0 ? [] : [dir, path3.join(tmpdir(), path3.basename(dir))]))];
}
function executionPath(root, value2) {
  return value2.split(path3.delimiter).filter((p) => p && path3.isAbsolute(p) && !inside(root, p) && !p.split(path3.sep).includes("node_modules")).join(path3.delimiter) || "/usr/bin:/bin";
}
async function materializationDepth(input, git) {
  if (input.gitContext === "none") return 1;
  const { headSha, base } = input.candidate;
  try {
    for (const tip of [headSha, base.tipSha]) {
      const boundary = (await git("-C", input.rootDir, "rev-list", "--boundary", `${base.mergeBaseSha}..${tip}`)).split("\n").filter((line) => line.startsWith("-"));
      if (tip !== base.mergeBaseSha && boundary.join("\n") !== `-${base.mergeBaseSha}`) return void 0;
    }
    const counts = await Promise.all([headSha, base.tipSha].map((tip) => git("-C", input.rootDir, "rev-list", "--count", `${base.mergeBaseSha}..${tip}`)));
    const longest = Math.max(...counts.map(Number));
    return Number.isSafeInteger(longest) && longest >= 0 ? longest + 1 : void 0;
  } catch {
    return void 0;
  }
}
async function createCheckSnapshot(input) {
  if (input.physicalSource !== void 0) return createFiniteCheckSnapshot(input);
  const report2 = (phase, started) => {
    try {
      input.onPhase?.(phase, performance.now() - started);
    } catch {
    }
  };
  const measured = phaseMeter(input.onPhase);
  const materializationStarted = performance.now();
  const rootDir = await realpath(await mkdtemp(path3.join(tmpdir(), "delivery-check-")));
  const cleanEnv = { ...input.environment, PATH: executionPath(input.rootDir, input.environment["PATH"] ?? process.env["PATH"] ?? "/usr/bin:/bin"), GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0", GIT_AUTHOR_DATE: "2000-01-01T00:00:00Z", GIT_COMMITTER_DATE: "2000-01-01T00:00:00Z" };
  let privateControl;
  let gitDirectory;
  const git = async (...args) => {
    const controller = new AbortController();
    let timedOut = false;
    const deadline = performance.now() + SNAPSHOT_TIMEOUT_MS;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, SNAPSHOT_TIMEOUT_MS);
    const abort = () => controller.abort();
    input.signal?.addEventListener("abort", abort, { once: true });
    if (input.signal?.aborted) abort();
    try {
      const result = await createExecPort().run({
        command: "git",
        args: gitDirectory ? [`--git-dir=${gitDirectory}`, `--work-tree=${rootDir}`, ...args] : args,
        cwd: rootDir,
        env: cleanEnv,
        maxBuffer: 128 * 1024 * 1024,
        signal: controller.signal
      });
      timedOut ||= performance.now() >= deadline;
      if (timedOut || result.code !== 0) throw new CheckSnapshotError(timedOut ? "check_snapshot_timeout" : input.signal?.aborted ? "check_snapshot_interrupted" : "check_snapshot_unavailable", "Private Git materialization did not complete successfully.");
      return result.stdout.trim();
    } finally {
      clearTimeout(timer);
      input.signal?.removeEventListener("abort", abort);
    }
  };
  const cleanup = () => removeTrees([rootDir, ...privateControl ? [privateControl] : []]);
  try {
    await git("init", "-q");
    const depth = await materializationDepth(input, git);
    const mergeBase = input.gitContext !== "none" && depth !== void 0 ? [] : [input.candidate.base.mergeBaseSha];
    await git(
      "fetch",
      "--update-shallow",
      ...depth === void 0 ? [] : ["--depth", String(depth)],
      "--no-tags",
      "--no-write-fetch-head",
      input.rootDir,
      input.candidate.headSha,
      input.candidate.base.tipSha,
      ...mergeBase,
      input.candidate.treeSha
    );
    await git("update-ref", "HEAD", input.candidate.headSha);
    await git("read-tree", input.candidate.treeSha);
    await git("checkout-index", "--all", "--force");
    if ((await git("ls-tree", "-r", "--name-only", input.candidate.treeSha)).split("\n").some((p) => p.split("/").includes("node_modules"))) throw new CheckSnapshotError("check_dependency_source_overlap", "Tracked node_modules cannot be replaced by private dependency setup.");
    const candidateRef = "refs/delivery/candidate", baseRef = "refs/delivery/base";
    const candidateCommit = await git("-c", "user.name=Delivery", "-c", "user.email=delivery@example.invalid", "-c", "commit.gpgsign=false", "commit-tree", input.candidate.treeSha, "-p", input.candidate.headSha, "-m", "Prepared execution snapshot");
    await git("update-ref", candidateRef, candidateCommit);
    await git("update-ref", "refs/delivery/origin-head", input.candidate.headSha);
    await git("update-ref", "HEAD", candidateCommit);
    await git("update-ref", baseRef, input.candidate.base.tipSha);
    await git("update-ref", "refs/delivery/merge-base", input.candidate.base.mergeBaseSha);
    if (/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(input.candidate.base.ref)) await git("update-ref", input.candidate.base.ref.startsWith("refs/") ? input.candidate.base.ref : `refs/remotes/${input.candidate.base.ref}`, input.candidate.base.tipSha);
    if (input.gitContext === "none") {
      privateControl = await realpath(await mkdtemp(path3.join(tmpdir(), "delivery-check-control-")));
      gitDirectory = path3.join(privateControl, "repository");
      await rename(path3.join(rootDir, ".git"), gitDirectory);
    }
    const controlRoot = privateControl ?? path3.join(rootDir, ".git");
    const inventory = (root, outputs, sourceOnly = false, deadline = performance.now() + SNAPSHOT_TIMEOUT_MS) => snapshotInventory(root, outputs, sourceOnly, deadline, input.signal, input.gitContext === "none");
    report2("materialization", materializationStarted);
    const baseline = await measured("baseline-inventory", () => inventory(rootDir, input.outputs, Boolean(input.dependencies)));
    let dependencyDigest = baseline, clonable = true;
    const environmentFor = (root, control) => ({
      ...input.environment,
      PATH: `${path3.join(root, "node_modules/.bin")}${path3.delimiter}${cleanEnv["PATH"]}`,
      HOME: path3.join(control, "home"),
      TMPDIR: path3.join(control, "tmp"),
      GIT_CEILING_DIRECTORIES: path3.dirname(root),
      ...input.gitContext === "none" ? {} : {
        DELIVERY_CHECK_BASE_REF: baseRef,
        DELIVERY_CHECK_CANDIDATE_REF: candidateRef,
        DELIVERY_CHECK_ORIGIN_HEAD: input.candidate.headSha,
        DELIVERY_CHECK_ORIGIN_TREE: input.candidate.treeSha,
        DELIVERY_CHECK_MERGE_BASE: input.candidate.base.mergeBaseSha
      }
    });
    const environment = environmentFor(rootDir, controlRoot);
    await mkdir(environment["HOME"], { recursive: true });
    await mkdir(environment["TMPDIR"], { recursive: true });
    if (input.dependencies) {
      const started = performance.now();
      const result = await measured("dependencies", () => createExecPort().run({ command: input.dependencies.command[0], args: input.dependencies.command.slice(1), cwd: rootDir, env: environment, timeoutMs: input.dependencies.timeoutMs, maxBuffer: 1024 * 1024, ...input.signal ? { signal: input.signal } : {} }));
      input.onDependencyResult?.(result, Math.round(performance.now() - started));
      if (result.code !== 0 || input.signal?.aborted) throw new CheckSnapshotError("check_dependency_failed", "Private dependency installation did not complete successfully.");
      const installed = await measured("post-dependency-inventory", () => snapshotDigests(rootDir, input.outputs, false, performance.now() + SNAPSHOT_TIMEOUT_MS, input.signal, input.gitContext === "none", true, [], selfReferences(rootDir, privateControl)));
      if (baseline !== installed.sourceDigest) throw new CheckSnapshotError("check_snapshot_drift", "Dependency setup changed prepared source bytes.");
      dependencyDigest = installed.digest;
      clonable = !installed.selfReferenced;
    }
    const treeGit = (tree, deadline, accepted = [0], limit) => {
      const gitDir = tree.privateControl ? path3.join(tree.privateControl, "repository") : path3.join(tree.rootDir, ".git");
      return async (...args) => {
        const result = await snapshotProcess("git", [`--git-dir=${gitDir}`, `--work-tree=${tree.rootDir}`, "-c", "core.fileMode=true", "-c", "core.sparseCheckout=false", "-c", "core.symlinks=true", "-c", "core.fsmonitor=false", ...args], tree.rootDir, cleanEnv, deadline, input.signal, limit);
        if (!accepted.includes(result.code ?? -1)) throw new CheckSnapshotError("check_snapshot_unavailable", "Snapshot Git identity could not be read.");
        return result.stdout;
      };
    };
    const others = ["ls-files", "--others", "--directory", "--no-empty-directory", "-z"];
    const setupEntries = new Set(nulSeparated(await treeGit({ rootDir, ...privateControl ? { privateControl } : {}, owned: [] }, performance.now() + SNAPSHOT_TIMEOUT_MS)(...others)));
    const reusedVerification = "index";
    const open3 = (tree, onPhase) => {
      const control = tree.privateControl ?? path3.join(tree.rootDir, ".git");
      const verify = async (options = {}) => phaseMeter(onPhase)("verification", async () => {
        const timeoutMs = options.timeoutMs ?? SNAPSHOT_TIMEOUT_MS;
        if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > SNAPSHOT_TIMEOUT_MS) throw new CheckSnapshotError("check_snapshot_timeout", "Snapshot deadline must be positive and cannot exceed five minutes.");
        const deadline = performance.now() + timeoutMs;
        const verifyGit = treeGit(tree, deadline);
        if (options.unchanged && reusedVerification === "index") {
          const allowed = options.unchanged.allowed;
          await treeGit(tree, deadline, [0, 1])("update-index", "-q", "--refresh");
          if (nulSeparated(await treeGit(tree, deadline, [0], 128 * 1024 * 1024)("ls-files", "-v", "-z")).some((entry) => !entry.startsWith("H ")))
            throw new CheckSnapshotError("check_snapshot_drift", "A reused snapshot's index hides a tracked path.");
          if (input.gitContext === "none" && await lstat(path3.join(tree.rootDir, ".git")).then(() => true, (error) => error.code === "ENOENT" ? false : Promise.reject(new CheckSnapshotError("check_snapshot_unavailable", "Snapshot Git absence could not be read."))))
            throw new CheckSnapshotError("check_snapshot_drift", "A file-only snapshot gained Git metadata.");
          if (nulSeparated(await verifyGit("diff-files", "--name-only", "-z")).length) throw new CheckSnapshotError("check_snapshot_drift", "A reused snapshot changed tracked source.");
          for (const entry of nulSeparated(await verifyGit(...others))) {
            if (setupEntries.has(entry) || withinOutputs(entry, allowed)) continue;
            if (!entry.endsWith("/") || !allowed.some((output) => output.startsWith(entry))) throw new CheckSnapshotError("check_snapshot_drift", "A reused snapshot gained an undeclared untracked path.");
            if (nulSeparated(await verifyGit("ls-files", "--others", "-z", "--", `:(literal)${entry}`)).some((file) => !withinOutputs(file, allowed))) throw new CheckSnapshotError("check_snapshot_drift", "A reused snapshot gained an undeclared untracked path.");
          }
        } else {
          if (await inventory(tree.rootDir, options.outputs ?? input.outputs, false, deadline) !== dependencyDigest) throw new CheckSnapshotError("check_snapshot_drift", "Execution changed the private source, dependencies or pinned Git context.");
        }
        const indexTree = (await verifyGit("write-tree")).trim();
        const identities = (await verifyGit("rev-parse", "HEAD", `${candidateRef}^{tree}`, baseRef)).trim().split("\n");
        if (indexTree !== input.candidate.treeSha || identities.length !== 3 || identities[0] !== candidateCommit || identities[1] !== input.candidate.treeSha || identities[2] !== input.candidate.base.tipSha) throw new CheckSnapshotError("check_snapshot_drift", "Execution changed the private source, dependencies or pinned Git context.");
      });
      return {
        rootDir: tree.rootDir,
        commandRoot: path3.join(control, "commands"),
        environment: environmentFor(tree.rootDir, control),
        dependencyDigest,
        clonable,
        reusedVerification,
        verify,
        clone: (method, onClonePhase) => cloneSnapshot(tree, method, input.signal, onClonePhase, open3),
        cleanup: () => removeTrees(tree.owned)
      };
    };
    return open3({ rootDir, ...privateControl ? { privateControl } : {}, owned: [rootDir, ...privateControl ? [privateControl] : []] }, input.onPhase);
  } catch (error) {
    await cleanup();
    if (error instanceof CheckSnapshotError) throw error;
    throw new CheckSnapshotError("check_snapshot_unavailable", "The exact prepared Git tree could not be materialized privately.");
  }
}
function finiteSourceReaderRunner(input) {
  const deadline = performance.now() + SNAPSHOT_TIMEOUT_MS;
  const transportLimit = 128 * 1024 * 1024;
  let transportBytes = 0;
  return async (command, options) => {
    if (input.signal?.aborted) throw new CheckSnapshotError("check_snapshot_interrupted", "Finite source capture was cancelled.");
    const remaining = deadline - performance.now();
    if (remaining <= 0) throw new CheckSnapshotError("check_snapshot_timeout", "Finite source capture exceeded its deadline.");
    return await new Promise((resolve, reject) => {
      const child = spawn(command[0], command.slice(1), {
        cwd: options.cwd,
        env: { PATH: executionPath(input.rootDir, input.environment["PATH"] ?? process.env["PATH"] ?? "/usr/bin:/bin"), GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0", GIT_OPTIONAL_LOCKS: "0" },
        stdio: [options.stdin === void 0 ? "ignore" : "pipe", "pipe", "pipe"]
      });
      let failure2;
      const chunks = [];
      const stop = (code) => {
        failure2 ??= new CheckSnapshotError(code, "Finite source reader did not complete safely.");
        child.kill("SIGKILL");
      };
      const abort = () => stop("check_snapshot_interrupted"), timer = setTimeout(() => stop("check_snapshot_timeout"), remaining);
      input.signal?.addEventListener("abort", abort, { once: true });
      if (input.signal?.aborted) abort();
      child.stdout.on("data", (chunk) => {
        transportBytes += chunk.length;
        if (transportBytes > transportLimit) stop("check_snapshot_unavailable");
        else chunks.push(chunk);
      });
      child.stderr.on("data", () => {
      });
      child.once("error", () => {
        failure2 ??= new CheckSnapshotError("check_snapshot_unavailable", "Finite source reader failed to start.");
      });
      child.stdin?.once("error", () => stop("check_snapshot_unavailable"));
      child.stdin?.end(options.stdin);
      child.once("close", (code) => {
        clearTimeout(timer);
        input.signal?.removeEventListener("abort", abort);
        if (failure2) reject(failure2);
        else {
          const bytes = Buffer.concat(chunks);
          resolve({ exitCode: code ?? -1, stdout: bytes.toString("utf8"), stderr: "", ...options.captureBytes ? { stdoutBase64: bytes.toString("base64") } : {} });
        }
      });
    });
  };
}
async function createFiniteCheckSnapshot(input) {
  const materializationStarted = performance.now();
  const source = input.physicalSource;
  if (input.gitContext !== "none" || !isPhysicalSourceDefinition(source) || physicalSourceOutputOverlap(source, input.outputs))
    throw new CheckSnapshotError("check_physical_source_invalid", "Finite source requires Git-none and absences disjoint from all mutable outputs.");
  let captured;
  try {
    captured = await capturePhysicalSource(source, await candidateTreeSourceReader(input.rootDir, input.candidate.treeSha, finiteSourceReaderRunner(input)));
  } catch (error) {
    if (error instanceof CheckSnapshotError) throw error;
    throw new CheckSnapshotError("check_physical_source_invalid", "Finite source inputs are missing, linked or unsupported in the candidate.");
  }
  const rootDir = await realpath(await mkdtemp(path3.join(tmpdir(), "delivery-check-")));
  let control;
  const cleanup = () => removeTrees([rootDir, ...control ? [control] : []]);
  const measured = phaseMeter(input.onPhase);
  const inventory = async (root, outputs, sourceOnly = false, deadline) => {
    if (physicalSourceOutputOverlap(source, outputs)) throw new CheckSnapshotError("check_physical_source_invalid", "Mutable output overlaps a required absence.");
    return snapshotInventory(root, outputs, sourceOnly, deadline, input.signal, true, source.absentInputs);
  };
  try {
    control = await realpath(await mkdtemp(path3.join(tmpdir(), "delivery-check-control-")));
    for (const file of captured.files) {
      await mkdir(path3.dirname(path3.join(rootDir, file.file)), { recursive: true });
      await writeFile(path3.join(rootDir, file.file), file.bytes, { flag: "wx" });
      await chmod(path3.join(rootDir, file.file), file.mode === "100755" ? 493 : 420);
    }
    try {
      input.onPhase?.("materialization", performance.now() - materializationStarted);
    } catch {
    }
    const cleanPath = executionPath(input.rootDir, input.environment["PATH"] ?? process.env["PATH"] ?? "/usr/bin:/bin");
    const environmentFor = (root, controlDir) => ({
      ...input.environment,
      PATH: `${path3.join(root, "node_modules/.bin")}${path3.delimiter}${cleanPath}`,
      HOME: path3.join(controlDir, "home"),
      TMPDIR: path3.join(controlDir, "tmp"),
      GIT_CEILING_DIRECTORIES: path3.dirname(root)
    });
    const environment = environmentFor(rootDir, control);
    await mkdir(environment.HOME, { recursive: true });
    await mkdir(environment.TMPDIR, { recursive: true });
    const baseline = await measured("baseline-inventory", () => inventory(rootDir, input.outputs, Boolean(input.dependencies)));
    if (input.dependencies) {
      const start = performance.now();
      const result = await measured("dependencies", () => createExecPort().run({
        command: input.dependencies.command[0],
        args: input.dependencies.command.slice(1),
        cwd: rootDir,
        env: environment,
        timeoutMs: input.dependencies.timeoutMs,
        maxBuffer: 1024 * 1024,
        ...input.signal ? { signal: input.signal } : {}
      }));
      input.onDependencyResult?.(result, Math.round(performance.now() - start));
      if (result.code !== 0 || input.signal?.aborted) throw new CheckSnapshotError("check_dependency_failed", "Private finite dependency installation failed.");
    }
    const installed = input.dependencies ? await measured("post-dependency-inventory", () => snapshotDigests(rootDir, input.outputs, false, performance.now() + SNAPSHOT_TIMEOUT_MS, input.signal, true, true, source.absentInputs, selfReferences(rootDir, control))) : { digest: baseline, sourceDigest: baseline, selfReferenced: false };
    if (baseline !== installed.sourceDigest) throw new CheckSnapshotError("check_snapshot_drift", "Dependency setup changed finite source bytes or modes.");
    const open3 = (tree, onPhase) => {
      const controlDir = tree.privateControl;
      const verify = async (options = {}) => phaseMeter(onPhase)("verification", async () => {
        const timeout = options.timeoutMs ?? SNAPSHOT_TIMEOUT_MS;
        if (!Number.isFinite(timeout) || timeout <= 0 || timeout > SNAPSHOT_TIMEOUT_MS) throw new CheckSnapshotError("check_snapshot_timeout", "Invalid finite verification deadline.");
        if (await inventory(tree.rootDir, options.outputs ?? input.outputs, false, performance.now() + timeout) !== installed.digest)
          throw new CheckSnapshotError("check_snapshot_drift", "Finite source or installed dependencies changed.");
      });
      return {
        rootDir: tree.rootDir,
        commandRoot: path3.join(controlDir, "commands"),
        environment: environmentFor(tree.rootDir, controlDir),
        dependencyDigest: installed.digest,
        clonable: !installed.selfReferenced,
        reusedVerification: "inventory",
        verify,
        clone: (method, onClonePhase) => cloneSnapshot(tree, method, input.signal, onClonePhase, open3),
        cleanup: () => removeTrees(tree.owned)
      };
    };
    return open3({ rootDir, privateControl: control, owned: [rootDir, control] }, input.onPhase);
  } catch (error) {
    await cleanup();
    if (error instanceof CheckSnapshotError) throw error;
    throw new CheckSnapshotError("check_snapshot_unavailable", "Finite source could not be materialized or verified.");
  }
}

// packages/cli/src/scoped-candidate.ts
import { execFile } from "node:child_process";
import { mkdtemp as mkdtemp2, rm as rm2 } from "node:fs/promises";
import path4 from "node:path";
import { tmpdir as tmpdir2 } from "node:os";
import { promisify } from "node:util";
import { captureGitCandidate } from "./kernel.mjs";
var exec = promisify(execFile);
async function captureScopedCandidate(options) {
  const dir = await mkdtemp2(path4.join(tmpdir2(), "delivery-index-"));
  const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))), GIT_INDEX_FILE: path4.join(dir, "index"), GIT_OPTIONAL_LOCKS: "0", GIT_TERMINAL_PROMPT: "0" };
  const run = async (command, invocation) => {
    try {
      const result = await exec(command[0], command.slice(1), { cwd: invocation.cwd, env, encoding: "buffer", maxBuffer: 64 * 1024 * 1024 });
      return { exitCode: 0, stdout: result.stdout.toString("utf8"), ...invocation.captureBytes ? { stdoutBase64: result.stdout.toString("base64") } : {}, stderr: result.stderr.toString("utf8") };
    } catch (error) {
      const failure2 = error;
      return { exitCode: typeof failure2.code === "number" ? failure2.code : -1, stdout: failure2.stdout?.toString("utf8") ?? "", stderr: failure2.stderr?.toString("utf8") ?? "Git capture failed" };
    }
  };
  try {
    for (const args of [["read-tree", "HEAD"], ["add", "--all"]]) {
      const result = await run(["git", ...args], { cwd: options.rootDir });
      if (result.exitCode !== 0) throw new Error("Cannot capture source into a private index");
    }
    return await captureGitCandidate({ ...options, run });
  } finally {
    await rm2(dir, { recursive: true, force: true });
  }
}

// packages/cli/src/boundary.ts
import path5 from "node:path";
import { randomUUID as randomUUID3 } from "node:crypto";
import { pathToFileURL } from "node:url";
import {
  BlockedError as BlockedError2,
  classifyExecutionContext,
  createArtifactsPort,
  createBlocker as createBlocker2,
  createCandidateCapture,
  createInternalErrorBlocker,
  evaluateCandidateActivation,
  renderBlockers,
  resolveRecordStorage as resolveRecordStorage2,
  submitManifest,
  validateHarnessConfig,
  withDeliverableIdentity
} from "./kernel.mjs";

// packages/cli/src/provider-rails.ts
import {
  DELIVERY_PROVIDER_RAILS_VERSION,
  consumeProviderRailMessages,
  openProviderRailProcess,
  invokeProviderRail
} from "./kernel.mjs";

// packages/cli/src/command-activity.ts
import { randomUUID as randomUUID2 } from "node:crypto";
var absent = { needsCandidate: false, start: async () => {
}, prompt: (prompt) => prompt, finish: async () => {
} };
function outcome(code) {
  return code === 0 ? "ok" : code === 2 ? "usage" : code === 130 ? "interrupted" : "policy";
}
function observeCandidateCaptures(capture2, observation) {
  let start;
  return async () => {
    const result = await capture2();
    if (result.ok) {
      start ??= Promise.resolve().then(() => observation.start(result.candidate.treeSha)).catch(() => {
      });
      await start;
    }
    return result;
  };
}
async function beginCommandObservation(cwd, command) {
  try {
    const resolved = await resolveRunSurface(cwd);
    if (!resolved.ok) return absent;
    const { store, commonDir, worktreeKey } = resolved.surface;
    const current = await store.current(worktreeKey);
    if (!current.ok || current.runId === void 0) return absent;
    const runId = current.runId;
    const history = await store.read(runId);
    if (!history.ok) return absent;
    const version = history.events[0]?.version ?? "run-event/1";
    const attemptId = `command-${randomUUID2()}`;
    const activityId = attemptId;
    let candidateTreeSha;
    async function emit(kind, payload) {
      try {
        await store.append(runId, buildRunEvent({
          runId,
          commonDir,
          kind,
          role: "cli",
          version,
          ...version === "run-event/2" ? { eventId: randomUUID2() } : {},
          payload
        }));
      } catch {
      }
    }
    const binding = () => ({ activityId, attemptId, candidateTreeSha });
    async function activity(state, nextStep) {
      if (version !== "run-event/2" || candidateTreeSha === void 0) return;
      await emit("activity.observed", { ...binding(), state, owner: "delivery-harness.cli", phase: command, nextStep });
    }
    return {
      needsCandidate: version === "run-event/2",
      async start(tree) {
        candidateTreeSha = tree;
        await activity("running", "Await command outcome; intermediate progress is unavailable unless explicitly observed.");
      },
      prompt(prompt) {
        return async (decision, obligations) => {
          if (version !== "run-event/2" || candidateTreeSha === void 0) return prompt(decision, obligations);
          const waitId = `wait-${randomUUID2()}`;
          const scope = "This command invocation and the waiver decision's candidate and obligations only; this observation grants no authority.";
          await activity("waiting", "Respond to the native waiver prompt.");
          await emit("wait.started", { ...binding(), waitId, owner: "operator", waitingOn: "human", reason: "The executing command opened its native waiver prompt.", nextAction: "Answer the native prompt.", scope });
          try {
            const answer = await prompt(decision, obligations);
            await emit("wait.resolved", { ...binding(), waitId, resolution: answer === false ? "Native prompt declined." : "Native prompt answered; approval remains subject to the gate.", scope });
            return answer;
          } catch (error) {
            await emit("wait.resolved", { ...binding(), waitId, resolution: "Native prompt ended without a decision.", scope });
            throw error;
          } finally {
            await activity("running", "Await command outcome.");
          }
        };
      },
      async finish(code, durationMs, digest, preparation) {
        await activity(code === 0 ? "completed" : code === 130 ? "interrupted" : "failed", `Command returned ${outcome(code)} (exit ${code}); continue the declared delivery workflow.`);
        await emit("command.completed", {
          command,
          outcome: outcome(code),
          durationMs,
          ...code === 0 && digest !== void 0 ? { digest } : {},
          ...version === "run-event/2" && command === "prepare" && code === 0 && preparation !== void 0 ? { preparation } : {}
        });
      }
    };
  } catch {
    return absent;
  }
}

// packages/cli/src/boundary.ts
var EXIT_OK = 0;
var EXIT_POLICY = 1;
var EXIT_USAGE = 2;
var EXIT_INTERRUPTED = 130;
var CliInterruption = class extends Error {
  constructor(message = "Interrupted.") {
    super(message);
    this.name = "CliInterruption";
  }
};
function isConfigFreeCommand(descriptor) {
  return descriptor.configFree === true;
}
function commandBlocker(input) {
  return createBlocker2({
    code: input.code,
    source: { kind: "command", id: input.sourceId },
    summary: input.summary,
    ...input.details === void 0 ? {} : { details: input.details },
    remediations: input.remediations
  });
}
async function importHarnessConfig(rootDir) {
  const configPath = path5.join(rootDir, "harness.config.ts");
  let loaded;
  try {
    const module = await import(pathToFileURL(configPath).href);
    loaded = module.default;
  } catch (error) {
    throw new BlockedError2([
      commandBlocker({
        code: "config_unloadable",
        sourceId: "delivery-harness.cli.config",
        summary: "The harness configuration could not be loaded.",
        details: `${configPath}: ${error instanceof Error ? error.message : String(error)}`,
        remediations: [
          {
            id: "create-harness-config",
            kind: "manual_action",
            summary: "Provide a valid harness.config.ts at the repository root."
          }
        ]
      })
    ]);
  }
  const validation = validateHarnessConfig(loaded);
  if (!validation.ok) {
    throw new BlockedError2(validation.blockers);
  }
  return validation.config;
}
async function wireRepo(rootDir, config) {
  const storageOptions = { storageNamespace: config.storageNamespace };
  const storage = await resolveRecordStorage2(rootDir, storageOptions);
  const captureOptions = {
    rootDir,
    config,
    workspaceId: storage.workspaceId,
    computeIdentity: withDeliverableIdentity()
  };
  const captureCandidate = config.scopedExecution ? () => captureScopedCandidate(captureOptions) : createCandidateCapture(captureOptions);
  const projectActivation = (candidate) => evaluateCandidateActivation({ rootDir, candidate, config });
  return { rootDir, workspaceId: storage.workspaceId, captureCandidate, projectActivation, storageOptions };
}
var NAME_GAP = 2;
var USAGE = (commands) => {
  const column = Math.max(0, ...commands.map((command) => command.name.length)) + NAME_GAP;
  return [
    "Usage: delivery-harness <command> [options]",
    "",
    "Commands:",
    ...commands.map((command) => `  ${command.name.padEnd(column)}${command.summary}`)
  ].join("\n");
};
var isHelpRequest = (args) => args.length === 1 && (args[0] === "--help" || args[0] === "-h");
var COMPLETION_WRAPPED_COMMANDS = [
  "admit",
  "check",
  "prepare",
  "review-context",
  "emit-review-evidence",
  "submit-evidence",
  "gate",
  "record",
  "verify"
];
async function runCliBoundary(argv, commands, runtime) {
  const [commandName, ...args] = argv;
  if (commandName === void 0 || commandName === "--help" || commandName === "-h" || commandName === "help") {
    runtime.stdout(`${USAGE(commands)}
`);
    return commandName === void 0 ? EXIT_USAGE : EXIT_OK;
  }
  const descriptor = commands.find((command) => command.name === commandName);
  if (descriptor === void 0) {
    runtime.stderr(`Unknown command: ${commandName}

${USAGE(commands)}
`);
    return EXIT_USAGE;
  }
  if (isHelpRequest(args)) {
    runtime.stdout(`${descriptor.usage}
`);
    return EXIT_OK;
  }
  if (isConfigFreeCommand(descriptor)) {
    return runConfigFreeCommand(descriptor, args, runtime);
  }
  const startedAt = Date.now();
  const observation = COMPLETION_WRAPPED_COMMANDS.includes(descriptor.name) ? await beginCommandObservation(runtime.cwd, descriptor.name) : void 0;
  let completed;
  const code = await runConfiguredCommand(descriptor, args, runtime, (value2) => {
    completed = value2;
  }, observation);
  if (!completed?.unobserved) await observation?.finish(code, Date.now() - startedAt, completed?.digest, completed?.preparation);
  return code;
}
async function runConfigFreeCommand(descriptor, args, runtime) {
  try {
    const result = await descriptor.run({
      rootDir: runtime.cwd,
      env: runtime.env,
      stdinIsTTY: runtime.stdinIsTTY,
      args,
      readStdin: runtime.readStdin ?? (async () => ""),
      write: (text2) => runtime.stdout(`${text2}
`),
      ...runtime.signal === void 0 ? {} : { signal: runtime.signal }
    });
    if (result.kind === "ok") {
      if (result.summary !== void 0 && result.summary !== "") runtime.stdout(`${result.summary}
`);
      return EXIT_OK;
    }
    if (result.kind === "usage") {
      runtime.stderr(`${result.message}
`);
      return EXIT_USAGE;
    }
    runtime.stderr(`${renderBlockers(result.blockers)}
`);
    return EXIT_POLICY;
  } catch (error) {
    if (error instanceof CliInterruption) {
      runtime.stderr(`${error.message}
`);
      return EXIT_INTERRUPTED;
    }
    if (error instanceof CheckSnapshotError) error = new BlockedError2([commandBlocker({ code: error.code, sourceId: "delivery-harness.cli.scoped-check", summary: error.message, remediations: [{ id: "repair-scoped-execution", kind: "manual_action", summary: "Repair the scoped execution inputs or profile, prepare and retry." }] })]);
    if (error instanceof BlockedError2) {
      runtime.stderr(`${renderBlockers(error.blockers)}
`);
      return EXIT_POLICY;
    }
    const blocker = createInternalErrorBlocker({
      source: { kind: "command", id: descriptor.sourceId },
      error,
      reproduce: ["delivery-harness", descriptor.name]
    });
    runtime.stderr(`${renderBlockers([blocker])}
`);
    return EXIT_POLICY;
  }
}
async function runConfiguredCommand(descriptor, args, runtime, observeSuccess, observation) {
  const loadConfig = runtime.loadConfig ?? importHarnessConfig;
  const artifacts = runtime.artifacts ?? createArtifactsPort();
  try {
    const config = await loadConfig(runtime.cwd);
    let wiringPromise;
    const wire = () => {
      wiringPromise ??= wireRepo(runtime.cwd, config).then((wiring) => descriptor.capturesCandidate && observation?.needsCandidate ? { ...wiring, captureCandidate: observeCandidateCaptures(wiring.captureCandidate, observation) } : wiring);
      return wiringPromise;
    };
    if (observation?.needsCandidate && !descriptor.capturesCandidate) {
      try {
        const capture2 = await (await wire()).captureCandidate();
        if (capture2.ok) await observation.start(capture2.candidate.treeSha);
      } catch {
      }
    }
    const context = {
      rootDir: runtime.cwd,
      config,
      ...runtime.policyBinding === void 0 ? {} : { policyBinding: runtime.policyBinding },
      env: runtime.env,
      stdinIsTTY: runtime.stdinIsTTY,
      stdoutIsTTY: runtime.stdoutIsTTY,
      args,
      wire,
      readStdin: runtime.readStdin ?? (async () => ""),
      artifacts,
      ...runtime.signal === void 0 ? {} : { signal: runtime.signal },
      // The waiver prompt is offered only under a real TTY. A non-interactive
      // invocation never prompts — it blocks — no matter what the run wired.
      ...runtime.stdinIsTTY && runtime.stdoutIsTTY && runtime.promptForWaiver !== void 0 ? { promptForWaiver: observation?.prompt(runtime.promptForWaiver) ?? runtime.promptForWaiver } : {},
      ...runtime.liveResults === void 0 ? {} : { liveResults: runtime.liveResults },
      invokeProvider: async ({ providerId, payload, requiresEvidence, signal: attemptSignal }) => {
        const provider = config.providers.find((registration) => registration.id === providerId);
        if (provider?.command === void 0) return void 0;
        const command = provider.command;
        const inherited = provider.inheritDescriptors;
        const requestId = randomUUID3();
        const allocation = await artifacts.allocateRunRoot({ providerId, runId: requestId });
        if (!allocation.ok) {
          throw new BlockedError2([
            commandBlocker({
              code: "provider_rail_run_root_refused",
              sourceId: "delivery-harness.cli.provider-rails",
              summary: "The provider run root could not be allocated.",
              details: `${providerId}/${requestId}: ${allocation.reason}`,
              remediations: [
                {
                  id: "check-provider-identity",
                  kind: "code_change",
                  summary: "Correct the provider id or restore the harness run-root location, then retry."
                }
              ]
            })
          ]);
        }
        const wiring = await wire();
        const interruptController = runtime.signal === void 0 ? new AbortController() : void 0;
        const onInterrupt = () => interruptController?.abort();
        if (interruptController !== void 0) process.once("SIGINT", onInterrupt);
        try {
          return await invokeProviderRail(
            {
              providerId,
              requestId,
              idempotencyKey: randomUUID3(),
              payload: { ...payload, runId: requestId, runRoot: allocation.runRoot.path },
              requiresEvidence
            },
            {
              // One call, so the test seam is given exactly what the real launch is.
              open: () => (runtime.openProviderRail ?? openProviderRailProcess)({
                providerId,
                command,
                cwd: runtime.cwd,
                env: { ...runtime.env },
                ...inherited ? { inheritDescriptors: inherited } : {}
              }),
              publishManifest: (manifestPath) => submitManifest(
                { rootDir: runtime.cwd, config, manifestPath },
                {
                  captureCandidate: wiring.captureCandidate,
                  artifacts,
                  expectedProviderAttempt: { providerId, runId: requestId, runRootPath: allocation.runRoot.path },
                  ...wiring.storageOptions
                }
              ),
              signal: [runtime.signal ?? interruptController?.signal, attemptSignal].filter((s) => s !== void 0).reduce((one, other) => one ? AbortSignal.any([one, other]) : other, void 0),
              cancellationId: randomUUID3()
            }
          );
        } finally {
          if (interruptController !== void 0) process.off("SIGINT", onInterrupt);
        }
      },
      write: (text2) => runtime.stdout(`${text2}
`),
      writeError: (text2) => runtime.stderr(`${text2}
`),
      classifyContext: () => classifyExecutionContext({
        config,
        env: runtime.env,
        stdinIsTTY: runtime.stdinIsTTY,
        stdoutIsTTY: runtime.stdoutIsTTY
      })
    };
    const result = await descriptor.run(context);
    if (runtime.signal?.aborted) throw new CliInterruption();
    if (result.kind === "ok") {
      observeSuccess(result);
      if (result.summary !== void 0 && result.summary !== "") runtime.stdout(`${result.summary}
`);
      return EXIT_OK;
    }
    if (result.kind === "usage") {
      runtime.stderr(`${result.message}
`);
      return EXIT_USAGE;
    }
    runtime.stderr(`${renderBlockers(result.blockers)}
`);
    return EXIT_POLICY;
  } catch (error) {
    if (error instanceof CliInterruption) {
      runtime.stderr(`${error.message}
`);
      return EXIT_INTERRUPTED;
    }
    if (error instanceof CheckSnapshotError) error = new BlockedError2([commandBlocker({ code: error.code, sourceId: "delivery-harness.cli.scoped-check", summary: error.message, remediations: [{ id: "repair-scoped-execution", kind: "manual_action", summary: "Repair the scoped execution inputs or profile, prepare and retry." }] })]);
    if (error instanceof BlockedError2) {
      runtime.stderr(`${renderBlockers(error.blockers)}
`);
      return EXIT_POLICY;
    }
    const blocker = createInternalErrorBlocker({
      source: { kind: "command", id: descriptor.sourceId },
      error,
      reproduce: ["delivery-harness", descriptor.name]
    });
    runtime.stderr(`${renderBlockers([blocker])}
`);
    return EXIT_POLICY;
  }
}

// packages/cli/src/review-evidence.ts
import { randomUUID as randomUUID4 } from "node:crypto";
import { mkdir as mkdir2, writeFile as writeFile2 } from "node:fs/promises";
import path6 from "node:path";
import {
  resolveReviewCharters,
  validateReviewedContext,
  parseReviewOutcome,
  deriveTelemetry,
  reviewerLists,
  capturePortableEvidenceContext,
  repositoryEvidenceReader,
  createArtifactsPort as createArtifactsPort2,
  ReviewInputError as OutcomeError,
  BlockedError as BlockedError3,
  digestCanonical,
  evaluatePreparationReceipt,
  sha256Hex as sha256Hex2,
  REVIEW_GREEN_1,
  REVIEW_GREEN_2
} from "./kernel.mjs";
import { validateReviewedContext as validateReviewedContext2, parseReviewOutcome as parseReviewOutcome2, deriveTelemetry as deriveTelemetry2, reviewerLists as reviewerLists2, REVIEWER_RESULTS } from "./kernel.mjs";
var REVIEW_PAYLOAD_SPECS = [REVIEW_GREEN_2, REVIEW_GREEN_1];
var ENVELOPE_SPEC = "delivery-evidence/1";
var EMITTER_VERSION = "1.0.0";
var REVIEW_CONTEXT_SPEC = "review-context/1";
function manifestCandidate(captured) {
  return {
    vcs: captured.vcs,
    treeSha: captured.treeSha,
    headSha: captured.headSha,
    deliverable: { digest: captured.deliverable.digest, identity: captured.deliverable.identity },
    base: { ref: captured.base.ref, tipSha: captured.base.tipSha, mergeBaseSha: captured.base.mergeBaseSha },
    workspaceId: captured.workspaceId
  };
}
async function buildReviewContext(rootDir, config, candidate, receipt2) {
  const inputs = await capturePortableEvidenceContext(config, repositoryEvidenceReader(rootDir, createArtifactsPort2()), receipt2.preparationFingerprint, candidate.deliverable.digest);
  const charters = inputs.reviewerCharters;
  if (charters.length === 0 || inputs.release === null) throw new OutcomeError("the compiled policy activates no review lens or installed release");
  const binding = {
    gate: resolveGateBinding(config),
    candidate: manifestCandidate(candidate),
    preparationFingerprint: inputs.preparationFingerprint,
    configurationDigest: inputs.configurationDigest,
    policyDigest: inputs.policyDigest,
    release: inputs.release,
    workflowGraphSha256: inputs.workflowGraphSha256,
    charters
  };
  return { spec: REVIEW_CONTEXT_SPEC, digest: digestCanonical(binding), binding };
}
function resolveGateBinding(config) {
  const obligations = config.obligations.filter(
    (obligation2) => REVIEW_PAYLOAD_SPECS.some((payloadSpec) => obligation2.acceptedPayloadSpecs.includes(payloadSpec))
  );
  if (obligations.length !== 1) {
    throw new OutcomeError(
      `the gate declares ${obligations.length} obligations accepting a supported review.green payload; this provider serves exactly one`
    );
  }
  const obligation = obligations[0];
  if (obligation.providers.length !== 1) {
    throw new OutcomeError(
      `obligation ${obligation.id} names ${obligation.providers.length} providers; this provider serves exactly one`
    );
  }
  return { obligationId: obligation.id, providerId: obligation.providers[0] };
}
function resolveReviewPayloadSpec(config, obligationId) {
  const obligation = config.obligations.find((entry) => entry.id === obligationId);
  const payloadSpec = REVIEW_PAYLOAD_SPECS.find((candidate) => obligation?.acceptedPayloadSpecs.includes(candidate));
  if (payloadSpec === void 0) throw new OutcomeError(`obligation ${obligationId} accepts no supported review.green payload`);
  return payloadSpec;
}
async function emitReviewEvidence(context, original, document) {
  const { rootDir, config } = context;
  const wiring = await context.wire();
  const capture2 = await wiring.captureCandidate();
  if (!capture2.ok) throw new OutcomeError(`the candidate could not be captured: ${capture2.code}`);
  const captured = capture2.candidate;
  const preparation = await evaluatePreparationReceipt(rootDir, { config, candidate: captured }, wiring.storageOptions);
  if (!preparation.prepared) throw new BlockedError3([...preparation.blockers]);
  const current = await buildReviewContext(rootDir, config, captured, preparation.receipt);
  validateReviewedContext(original, current, document);
  const charters = current.binding.charters.map((charter) => charter.reviewerId).sort();
  const outcome2 = parseReviewOutcome(document, charters);
  const binding = current.binding.gate;
  const payloadSpec = resolveReviewPayloadSpec(config, binding.obligationId);
  const candidate = manifestCandidate(captured);
  const provider = {
    id: binding.providerId,
    version: EMITTER_VERSION,
    // One emitter run is one evaluated pass over this candidate.
    runId: `r-${randomUUID4()}`,
    finalPassId: outcome2.finalPassId ?? "pass-1"
  };
  const reviewed = original;
  const originalRunHistory = outcome2.runHistory ?? [{
    preparedTreeSha: reviewed.binding.candidate.treeSha,
    evaluatedInPassId: provider.finalPassId
  }];
  const finalEntry = originalRunHistory.at(-1);
  if (finalEntry["preparedTreeSha"] !== reviewed.binding.candidate.treeSha || finalEntry["evaluatedInPassId"] !== provider.finalPassId) {
    throw new OutcomeError("the supplied final review pass does not name the original reviewed candidate");
  }
  const runHistory = originalRunHistory.map((entry, index) => index === originalRunHistory.length - 1 ? { ...entry, preparedTreeSha: captured.treeSha } : entry);
  const allocation = await context.artifacts.allocateRunRoot({ providerId: provider.id, runId: provider.runId });
  if (!allocation.ok) throw new OutcomeError(`the run root was refused: ${allocation.reason}`);
  const runRoot = allocation.runRoot.path;
  const lists = reviewerLists(charters, outcome2);
  await mkdir2(path6.join(runRoot, "reviewers"), { recursive: true });
  const artifacts = [];
  for (const [name, value2] of [["review-context", original], ["review-outcome", document]]) {
    const bytes = `${JSON.stringify(value2, null, 2)}
`;
    await writeFile2(path6.join(runRoot, `${name}.json`), bytes, "utf8");
    artifacts.push({ path: `${name}.json`, sha256: sha256Hex2(bytes), role: name });
  }
  if (digestCanonical(reviewed.binding.candidate) !== digestCanonical(candidate)) {
    const bytes = `${JSON.stringify({
      spec: "review-context-projection/1",
      basis: "unchanged-deliverable-and-review-inputs",
      originalContextDigest: reviewed.digest,
      reviewedCandidate: reviewed.binding.candidate,
      preparedCandidate: candidate,
      originalRunHistory,
      reviewRoundAdded: false
    }, null, 2)}
`;
    await writeFile2(path6.join(runRoot, "review-context-projection.json"), bytes, "utf8");
    artifacts.push({ path: "review-context-projection.json", sha256: sha256Hex2(bytes), role: "review-context-projection" });
  }
  for (const reviewerId of lists.approved) {
    const stamp = `${JSON.stringify(
      {
        schemaVersion: 1,
        reviewerId,
        result: "approved",
        provider: { id: provider.id, runId: provider.runId, finalPassId: provider.finalPassId },
        workspaceId: candidate.workspaceId,
        candidate
      },
      null,
      2
    )}
`;
    const relativePath = `reviewers/${reviewerId}.json`;
    await writeFile2(path6.join(runRoot, relativePath), stamp, "utf8");
    artifacts.push({ path: relativePath, sha256: sha256Hex2(stamp), role: "reviewer-approval" });
  }
  const manifest = {
    spec: ENVELOPE_SPEC,
    provider,
    candidate,
    repository: null,
    runHistory,
    artifacts,
    attestation: { level: "self", signatures: [] },
    recordedAt: (/* @__PURE__ */ new Date()).toISOString(),
    claims: [
      {
        obligation: binding.obligationId,
        payloadSpec,
        payload: {
          verdict: outcome2.verdict,
          finalized: true,
          editedAfterFinalPass: false,
          reviewers: {
            selected: lists.selected,
            completed: lists.completed,
            failed: lists.failed,
            timedOut: lists.timedOut
          },
          findings: outcome2.findings,
          telemetry: { ...deriveTelemetry(outcome2.findings, runHistory.length), ...outcome2.cost === void 0 ? {} : { cost: outcome2.cost } }
        }
      }
    ]
  };
  const manifestPath = path6.join(runRoot, "manifest.json");
  await writeFile2(manifestPath, `${JSON.stringify(manifest, null, 2)}
`, "utf8");
  return { manifestPath, runRoot };
}

// packages/cli/src/scoped-checks.ts
import { randomUUID as randomUUID6 } from "node:crypto";
import { access, mkdir as mkdir4, readFile as readFile3, realpath as realpath2, rename as rename2, rm as rm3, stat as stat2, writeFile as writeFile4 } from "node:fs/promises";
import path10 from "node:path";
import {
  deliveryRecordPathFor as deliveryRecordPathFor2,
  recordOutputProvider,
  computeDeliverableIdentity,
  capturePhysicalSource as capturePhysicalSource2,
  captureCheckBindings,
  captureCheckOutputSnapshots,
  candidateTreeEvidenceReader,
  candidateTreeSourceReader as candidateTreeSourceReader2,
  createExecPort as createExecPort3,
  digestCanonical as digestCanonical5,
  resolveRecordStorage as resolveRecordStorage5,
  runGitCommand,
  scopedCheckIdentity,
  selectScopedCheckAttempt,
  sha256Hex as sha256Hex3,
  submitManifest as submitManifest2,
  createResourceApplicabilityProjection,
  matchResourceApplicabilityProjection,
  createResourceApplicabilityBinding,
  reconstructResourceApplicability,
  discoverRecords,
  portableArtifactContents,
  isRecordFreshForCandidate,
  isRecordNeutralPath,
  normalizeInstalledDataResourceRequests as normalizeInstalledDataResourceRequests2
} from "./kernel.mjs";

// packages/cli/src/scoped-attempts.ts
import { randomUUID as randomUUID5 } from "node:crypto";
import { link, mkdir as mkdir3, readFile as readFile2, readdir as readdir2, unlink, writeFile as writeFile3 } from "node:fs/promises";
import path7 from "node:path";
import { digestCanonical as digestCanonical2, validateResourceApplicabilityProjection } from "./kernel.mjs";
var corrupt = () => new CheckSnapshotError("check_attempt_corrupt", "Scoped check attempt history is missing, corrupt or inconsistent.");
var gitSha = (value2) => typeof value2 === "string" && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value2);
function validateResourceOrigin(row) {
  if (row.executionHeadSha !== void 0 && !gitSha(row.executionHeadSha)) throw corrupt();
  const resource = row.payload?.resourceApplicability;
  if (resource === void 0) return;
  if (row.attempt.status !== "passed" || !row.executionHeadSha) throw corrupt();
  try {
    const projection = validateResourceApplicabilityProjection(resource);
    const attempt = row.attempt;
    const origin = {
      providerId: attempt.providerId,
      runId: attempt.origin.runId,
      attemptId: attempt.attemptId,
      generation: attempt.generation,
      inputDigest: attempt.inputDigest,
      profileDigest: attempt.profileDigest,
      source: { headSha: row.executionHeadSha, treeSha: attempt.origin.candidate.treeSha },
      status: "passed",
      superseded: false
    };
    if (digestCanonical2(projection.origin) !== digestCanonical2(origin)) throw corrupt();
  } catch {
    throw corrupt();
  }
}
var AttemptStore = class {
  root;
  constructor(root) {
    this.root = root;
  }
  async generations() {
    let names;
    try {
      names = await readdir2(this.root);
    } catch (error) {
      if (error.code === "ENOENT") return [];
      throw error;
    }
    if (names.some((n) => !/^[1-9][0-9]*$/.test(n) || !Number.isSafeInteger(Number(n)))) throw corrupt();
    return names.map(Number).sort((a, b) => a - b);
  }
  async allocate(input, options = {}) {
    if (options.executionHeadSha !== void 0 && !gitSha(options.executionHeadSha)) throw corrupt();
    await mkdir3(this.root, { recursive: true, mode: 448 });
    let generation = Math.max(0, ...await this.generations()) + 1;
    for (; ; ) {
      const dir = path7.join(this.root, String(generation));
      try {
        await mkdir3(dir, { mode: 448 });
      } catch (error) {
        if (error.code === "EEXIST") {
          generation++;
          continue;
        }
        throw error;
      }
      const attempt = { ...input, generation, attemptId: randomUUID5(), status: "running" };
      await this.publish(path7.join(dir, "running.json"), { attempt, ...options.executionHeadSha !== void 0 ? { executionHeadSha: options.executionHeadSha } : {} });
      return attempt;
    }
  }
  async publish(target, entry) {
    const temp = `${target}.${randomUUID5()}.tmp`;
    try {
      await writeFile3(temp, JSON.stringify({ digest: digestCanonical2(entry), entry }), { flag: "wx", mode: 384 });
      await link(temp, target);
    } finally {
      await unlink(temp).catch(() => void 0);
    }
  }
  async finish(attempt, status, payload) {
    const rows = await this.read();
    const running = rows.find((row) => row.attempt.generation === attempt.generation);
    if (running?.attempt.status !== "running" || digestCanonical2(running.attempt) !== digestCanonical2(attempt)) throw corrupt();
    const terminal = {
      attempt: { ...attempt, status },
      payload,
      ...running.executionHeadSha !== void 0 ? { executionHeadSha: running.executionHeadSha } : {}
    };
    validateResourceOrigin(terminal);
    await this.publish(this.terminalPath(attempt), terminal);
  }
  terminalPath(attempt) {
    return path7.join(this.root, String(attempt.generation), "terminal.json");
  }
  async load(file) {
    const raw = JSON.parse(await readFile2(file, "utf8"));
    const row = raw.entry;
    const attempt = row?.attempt;
    const nonempty = (value2) => typeof value2 === "string" && value2.length > 0;
    const candidate = attempt?.origin?.candidate;
    if (!row || !attempt || raw.digest !== digestCanonical2(row) || attempt.version !== "scoped-attempt/1" || !nonempty(attempt.attemptId) || !nonempty(attempt.providerId) || !nonempty(attempt.origin?.runId) || !Number.isSafeInteger(attempt.generation) || attempt.generation < 1 || !["running", "passed", "failed", "interrupted"].includes(attempt.status) || ![attempt.inputDigest, attempt.profileDigest].every((value2) => typeof value2 === "string" && /^[a-f0-9]{64}$/.test(value2)) || !candidate || ![
      candidate.treeSha,
      candidate.deliverableDigest,
      candidate.identityToken,
      candidate.baseRef,
      candidate.baseTipSha,
      candidate.mergeBaseSha,
      candidate.workspaceId
    ].every(nonempty)) throw corrupt();
    if (row.payload?.durationMs !== void 0 && (typeof row.payload.durationMs !== "number" || !Number.isFinite(row.payload.durationMs) || row.payload.durationMs < 0)) throw corrupt();
    validateResourceOrigin(row);
    return row;
  }
  async read() {
    try {
      const rows = [];
      for (const generation of await this.generations()) {
        const dir = path7.join(this.root, String(generation));
        const start = await this.load(path7.join(dir, "running.json"));
        if (start.attempt.status !== "running" || start.attempt.generation !== generation) throw corrupt();
        let terminal;
        try {
          terminal = await this.load(path7.join(dir, "terminal.json"));
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
        if (terminal && (terminal.executionHeadSha !== start.executionHeadSha || terminal.attempt.status === "running" || digestCanonical2({ ...terminal.attempt, status: "running" }) !== digestCanonical2(start.attempt))) throw corrupt();
        rows.push(terminal ?? start);
      }
      return rows;
    } catch {
      throw corrupt();
    }
  }
};

// packages/cli/src/scoped-installed-data.ts
import { captureInstalledDataBinding as capture, InstalledDataSourceError } from "./kernel.mjs";
async function captureInstalledDataBinding(...args) {
  try {
    return await capture(...args);
  } catch (error) {
    if (error instanceof InstalledDataSourceError) throw new CheckSnapshotError(error.code, error.message);
    throw error;
  }
}

// packages/cli/src/installed-data-resources.ts
import {
  createExecPort as createExecPort2,
  captureInstalledDataResources,
  normalizeInstalledDataResourceRequests,
  InstalledDataResourceError,
  INSTALLED_DATA_LIMITS
} from "./kernel.mjs";

// packages/cli/src/installed-data-worker.ts
var installedDataWorker = String.raw`
import { lstat, realpath, readdir, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
const fail=(code)=>{const e=new Error(code);e.code=code;throw e;};
const input=JSON.parse(process.env.DELIVERY_INSTALLED_DATA_REQUEST);
const root=await realpath(process.cwd());
const entries=Object.create(null), resolutions=Object.create(null);
let rawBytes=0;
const safe=(value)=>typeof value==='string'&&value.length>0&&!value.includes('\\')&&!value.startsWith('/')&&value.split('/').every(p=>p&&p!=='.'&&p!=='..');
const absolute=(value)=>{if(!safe(value))fail('installed_data_resource_unsafe');return path.join(root,value);};
async function statOne(relative){
 if(entries[relative])return entries[relative];
 if(Object.keys(entries).length>=input.limits.entries)fail('installed_data_limit_exceeded');
 let s;try{s=await lstat(absolute(relative));}catch(e){if(e.code!=='ENOENT')throw e;return entries[relative]={kind:'absent',physicalPath:relative,linkTarget:null};}
 if(s.isSymbolicLink())fail('installed_data_resource_unsafe');
 const physical=path.relative(root,await realpath(absolute(relative))).split(path.sep).join('/');
 if(physical!==relative)fail('installed_data_resource_unsafe');
 const kind=s.isFile()?'file':s.isDirectory()?'directory':'unsupported';
 if(kind==='unsupported')fail('installed_data_resource_unsafe');
 return entries[relative]={kind,physicalPath:physical,linkTarget:null};
}
async function observe(relative){
 const parts=relative.split('/');
 for(let i=1;i<parts.length;i++){
  const ancestor=await statOne(parts.slice(0,i).join('/'));
  if(ancestor.kind!=='directory'&&ancestor.kind!=='absent')fail('installed_data_resource_unsafe');
 }
 return statOne(relative);
}
async function read(relative){
 const entry=await observe(relative);if(entry.kind!=='file')fail('installed_data_resource_unsafe');
 if(entry.bytesBase64!==undefined)return;
 const handle=await open(absolute(relative),constants.O_RDONLY|constants.O_NOFOLLOW);
 try{
  const s=await handle.stat();if(!s.isFile())fail('installed_data_resource_unsafe');
  if(s.size>input.limits.rawBytes-rawBytes)fail('installed_data_limit_exceeded');
  const bytes=Buffer.alloc(s.size+1);const {bytesRead}=await handle.read(bytes,0,bytes.length,0);
  if(bytesRead!==s.size)fail('installed_data_resource_unsafe');
  rawBytes+=bytesRead;entry.bytesBase64=bytes.subarray(0,bytesRead).toString('base64');
 }finally{await handle.close();}
}
try{
 for(const r of input.requests){
  const issuer=await observe(r.issuer);if(issuer.kind!=='file')fail('installed_data_resource_unsafe');
  const require=createRequire(absolute(r.issuer));
  const paths=require.resolve.paths(r.package+'/package.json');
  const boundary=paths?.indexOf(path.join(root,'node_modules'))??-1;
  if(boundary<0)fail('installed_data_resolution_unsafe');
  const search=[];
  for(const dir of paths.slice(0,boundary+1)){
   const candidate=path.relative(root,path.join(dir,r.package)).split(path.sep).join('/');
   if(!safe(candidate))fail('installed_data_resolution_unsafe');
   const entry=await observe(candidate);search.push({path:candidate,kind:entry.kind});
   if(search.length<=boundary&&entry.kind!=='absent')fail('installed_data_resolution_unsafe');
  }
  const packageRoot='node_modules/'+r.package, manifestPath=packageRoot+'/package.json';
  await read(manifestPath);
  let resolved;try{resolved=require.resolve(r.package+'/package.json');}catch{fail('installed_data_resolution_unsafe');}
  if(resolved!==absolute(manifestPath))fail('installed_data_resolution_unsafe');
  resolutions[r.id]={mode:'require',packageRoot,manifestPath,search};
  for(const f of r.files)await read(packageRoot+'/'+f);
  for(const d of r.directories){
   const directory=packageRoot+'/'+d.path,entry=await observe(directory);
   if(entry.kind!=='directory')fail('installed_data_resource_unsafe');
   const names=await readdir(absolute(directory));if(names.length>input.limits.directoryMembers)fail('installed_data_limit_exceeded');
   entry.members=names.sort();
   for(const name of names)if(name.endsWith(d.suffix))await read(directory+'/'+name);
  }
 }
 const result=JSON.stringify({entries,resolutions});
 if(Buffer.byteLength(result)>input.limits.serializedBytes)fail('installed_data_limit_exceeded');
 process.stdout.write(result);
}catch(e){
 const allowed=['installed_data_descriptor_invalid','installed_data_resolution_unsafe','installed_data_resource_unsafe','installed_data_limit_exceeded'];
 process.stderr.write(allowed.includes(e.code)?e.code:'installed_data_resource_unsafe');process.exitCode=1;
}
`;

// packages/cli/src/installed-data-resources.ts
async function captureOwnedInstalledDataResources(snapshot, requests, context, options = {}) {
  const normalized = normalizeInstalledDataResourceRequests(requests);
  const timeoutMs = options.timeoutMs ?? 6e4;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 6e4)
    throw new InstalledDataResourceError("installed_data_descriptor_invalid", "Resource observer requires a bounded positive deadline.");
  if (options.signal?.aborted)
    throw new InstalledDataResourceError("installed_data_resource_unsafe", "Resource observation was cancelled before it started.");
  const request = JSON.stringify({ requests: normalized, limits: INSTALLED_DATA_LIMITS });
  if (Buffer.byteLength(request) > 64 * 1024) throw new InstalledDataResourceError("installed_data_limit_exceeded", "Resource request exceeds the bounded observer input.");
  await snapshot.verify({ outputs: [] });
  try {
    const result = await createExecPort2().run({
      command: process.execPath,
      args: ["--input-type=module", "--eval", installedDataWorker],
      cwd: snapshot.rootDir,
      env: { DELIVERY_INSTALLED_DATA_REQUEST: request },
      timeoutMs,
      maxBuffer: INSTALLED_DATA_LIMITS.serializedBytes,
      ...options.signal ? { signal: options.signal } : {}
    });
    if (result.code !== 0) {
      const code = result.stderr.trim();
      if (code === "installed_data_resolution_unsafe" || code === "installed_data_resource_unsafe" || code === "installed_data_limit_exceeded")
        throw new InstalledDataResourceError(code, "Owned installed resource observation refused.");
      throw new InstalledDataResourceError("installed_data_resource_unsafe", "Owned installed resource observation did not complete.");
    }
    let observed;
    try {
      observed = JSON.parse(result.stdout);
    } catch {
      throw new InstalledDataResourceError("installed_data_capture_invalid", "Owned resource observation returned malformed facts.");
    }
    const ports = {
      resolve: async (request2) => observed.resolutions[request2.id],
      stat: async (file) => {
        const entry = observed.entries[file];
        if (!entry) throw new InstalledDataResourceError("installed_data_capture_invalid", "Owned resource metadata is incomplete.");
        return { kind: entry.kind, physicalPath: entry.physicalPath, linkTarget: entry.linkTarget };
      },
      readFile: async (file) => {
        const entry = observed.entries[file];
        if (typeof entry?.bytesBase64 !== "string") throw new InstalledDataResourceError("installed_data_capture_invalid", "Owned resource bytes are incomplete.");
        return Buffer.from(entry.bytesBase64, "base64");
      },
      listDirectory: async (file) => {
        const entry = observed.entries[file];
        if (!Array.isArray(entry?.members)) throw new InstalledDataResourceError("installed_data_capture_invalid", "Owned directory membership is incomplete.");
        return entry.members;
      }
    };
    return await captureInstalledDataResources(normalized, context, ports);
  } finally {
    await snapshot.verify({ outputs: [] });
  }
}

// packages/cli/src/scoped-diagnostics.ts
import path9 from "node:path";
import { digestCanonical as digestCanonical4, resolveRecordStorage as resolveRecordStorage4 } from "./kernel.mjs";

// packages/cli/src/scoped-observations.ts
import path8 from "node:path";
import { digestCanonical as digestCanonical3, resolveRecordStorage as resolveRecordStorage3 } from "./kernel.mjs";
function projectScopedAttempt(attempt, durationMs) {
  return {
    version: attempt.version,
    providerId: attempt.providerId,
    attemptId: attempt.attemptId,
    generation: attempt.generation,
    inputDigest: attempt.inputDigest,
    profileDigest: attempt.profileDigest,
    status: attempt.status,
    origin: { runId: attempt.origin.runId, candidate: {
      treeSha: attempt.origin.candidate.treeSha,
      deliverableDigest: attempt.origin.candidate.deliverableDigest,
      identityToken: attempt.origin.candidate.identityToken,
      baseRef: attempt.origin.candidate.baseRef,
      baseTipSha: attempt.origin.candidate.baseTipSha,
      mergeBaseSha: attempt.origin.candidate.mergeBaseSha,
      workspaceId: attempt.origin.candidate.workspaceId
    } },
    ...durationMs === void 0 ? {} : { durationMs }
  };
}
async function readScopedCheckObservations(input) {
  const { rootDir, config } = input;
  const storage = await resolveRecordStorage3(rootDir, { storageNamespace: config.storageNamespace, leaf: "scoped-attempts" });
  const providers = [];
  for (const provider of config.providers.filter((provider2) => provider2.check?.scope !== void 0)) {
    const store = new AttemptStore(path8.join(storage.storageDir, digestCanonical3({ gate: config.gateId, provider: provider.id })));
    const retained = await store.read();
    if (retained.some((row) => row.attempt.providerId !== provider.id)) {
      throw new CheckSnapshotError("check_attempt_corrupt", "Scoped check attempt history belongs to a different provider.");
    }
    providers.push({ providerId: provider.id, attempts: retained.map(({ attempt, payload }) => projectScopedAttempt(attempt, payload?.durationMs)) });
  }
  return { version: "scoped-check-observations/1", providers };
}

// packages/cli/src/scoped-diagnostics.ts
var phases = ["snapshot-setup", "pre-command-verification", "command", "post-command-verification", "output-capture", "complete"];
var failureCodes = ["check_snapshot_interrupted", "check_snapshot_timeout", "check_snapshot_unavailable", "check_snapshot_escape", "check_snapshot_drift", "check_snapshot_cleanup_failed", "check_dependency_source_overlap", "check_dependency_failed", "check_command_failed", "check_output_missing", "check_attempt_superseded", "check_artifact_unavailable", "check_evidence_rejected", "check_cancelled"];
var executionCodes = ["ENOENT", "EACCES", "EPERM", "ABORT_ERR", "ERR_CHILD_PROCESS_STDIO_MAXBUFFER", "SIGKILL", "SIGTERM", "execution_failed"];
var setupPhases = ["materialization", "baseline-inventory", "dependencies", "post-dependency-inventory", "warm-base-wait", "clone", "pre-command-verification"];
function scopedDiagnosticFailure(error, executionErrorCode) {
  if (!(error instanceof CheckSnapshotError) || !failureCodes.includes(error.code)) return { unavailable: "unclassified" };
  return {
    code: error.code,
    ...executionCodes.includes(executionErrorCode ?? "") ? { executionErrorCode } : {}
  };
}
var INTERRUPTED_CREDENTIAL_PREFIX = 8;
function redactScopedOutput(text2, secrets) {
  const union = (spans2, [start, end]) => {
    const last = spans2.at(-1);
    if (last && last[1] >= start) last[1] = Math.max(last[1], end);
    else spans2.push([start, end]);
    return spans2;
  };
  let tail = text2.length;
  while (tail > 0 && text2.charCodeAt(tail - 1) === 65533) tail--;
  const spans = [];
  for (const secret of new Set(secrets.filter(Boolean))) {
    const border = [0];
    for (let i = 1, k = 0; i < secret.length; i++) {
      while (k > 0 && secret.charCodeAt(i) !== secret.charCodeAt(k)) k = border[k - 1];
      border.push(k += secret.charCodeAt(i) === secret.charCodeAt(k) ? 1 : 0);
    }
    const own = [];
    let matched = 0, atTail = 0;
    for (let i = 0; i < text2.length; i++) {
      if (i === tail) atTail = matched;
      if (matched >= INTERRUPTED_CREDENTIAL_PREFIX && text2.charCodeAt(i) !== secret.charCodeAt(matched)) union(own, [i - matched, i]);
      while (matched > 0 && text2.charCodeAt(i) !== secret.charCodeAt(matched)) matched = border[matched - 1];
      if (text2.charCodeAt(i) === secret.charCodeAt(matched)) matched++;
      if (matched === secret.length) {
        union(own, [i + 1 - matched, i + 1]);
        matched = border[matched - 1];
      }
    }
    if (tail === text2.length) atTail = matched;
    for (const span of own) spans.push(span);
    if (atTail > 0) spans.push([tail - atTail, text2.length]);
  }
  const merged = spans.sort((a, b) => a[0] - b[0]).reduce(union, []);
  return merged.map(([start], i) => text2.slice(i ? merged[i - 1][1] : 0, start) + "[REDACTED]").join("") + text2.slice(merged.at(-1)?.[1] ?? 0);
}
function redactScopedStreams(result, secrets) {
  return `${redactScopedOutput(result.stdout, secrets)}
${redactScopedOutput(result.stderr, secrets)}`;
}
function scopedCommandDiagnostic(result, secrets) {
  if (result.errorCode === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") return { unavailable: "not-completed" };
  const numericExit = result.errorCode === void 0 || result.errorCode === String(result.code);
  if (!numericExit && result.stdout.length + result.stderr.length === 0) return { unavailable: ["ENOENT", "EACCES", "EPERM"].includes(result.errorCode) ? "not-started" : "not-completed" };
  const redacted = redactScopedStreams(result, secrets);
  return { exitCode: numericExit ? result.code : null, outputTail: redacted.slice(-4e3), truncated: redacted.length > 4e3 };
}
function scopedDependencyDiagnostic(result, durationMs, secrets) {
  return {
    durationMs,
    command: scopedCommandDiagnostic(result, secrets),
    ...executionCodes.includes(result.errorCode ?? "") ? { executionErrorCode: result.errorCode } : {}
  };
}
function projectDiagnostic(value2) {
  const corrupt2 = () => {
    throw new CheckSnapshotError("check_attempt_corrupt", "Selected scoped diagnostics are malformed.");
  };
  const object = (v) => v !== null && typeof v === "object" && !Array.isArray(v) ? v : corrupt2();
  const row = object(value2), failure2 = object(row["failure"]);
  if (row["availability"] !== "available" || !phases.includes(row["phase"])) return corrupt2();
  let safeFailure;
  if ("unavailable" in failure2) {
    if (typeof failure2["unavailable"] !== "string" || !["not-failed", "unclassified"].includes(failure2["unavailable"]) || "code" in failure2 || "executionErrorCode" in failure2) return corrupt2();
    safeFailure = { unavailable: failure2["unavailable"] };
  } else {
    if (!failureCodes.includes(failure2["code"]) || "executionErrorCode" in failure2 && !executionCodes.includes(failure2["executionErrorCode"])) return corrupt2();
    safeFailure = { code: failure2["code"], ..."executionErrorCode" in failure2 ? { executionErrorCode: failure2["executionErrorCode"] } : {} };
  }
  const commandDiagnostic = (value3) => {
    const command = object(value3);
    if ("unavailable" in command) {
      if (typeof command["unavailable"] !== "string" || !["not-started", "not-completed"].includes(command["unavailable"]) || "exitCode" in command || "outputTail" in command || "truncated" in command) return corrupt2();
      return { unavailable: command["unavailable"] };
    }
    if (!(command["exitCode"] === null || typeof command["exitCode"] === "number" && Number.isSafeInteger(command["exitCode"]) && command["exitCode"] >= 0) || typeof command["outputTail"] !== "string" || command["outputTail"].length > 4e3 || typeof command["truncated"] !== "boolean") return corrupt2();
    return { exitCode: command["exitCode"], outputTail: command["outputTail"], truncated: command["truncated"] };
  };
  let dependency;
  if ("dependency" in row) {
    const value3 = object(row["dependency"]);
    if (typeof value3["durationMs"] !== "number" || !Number.isFinite(value3["durationMs"]) || value3["durationMs"] < 0 || "executionErrorCode" in value3 && !executionCodes.includes(value3["executionErrorCode"])) return corrupt2();
    dependency = {
      durationMs: value3["durationMs"],
      command: commandDiagnostic(value3["command"]),
      ..."executionErrorCode" in value3 ? { executionErrorCode: value3["executionErrorCode"] } : {}
    };
  }
  let setup;
  if ("setup" in row) {
    const value3 = object(row["setup"]);
    const one = (field2, allowed) => allowed.includes(value3[field2]) ? value3[field2] : corrupt2();
    const phasesValue = value3["phases"];
    if (!Array.isArray(phasesValue) || phasesValue.length > 16) return corrupt2();
    setup = {
      action: one("action", ["executed", "reused"]),
      snapshot: one("snapshot", ["built", "cloned", "retained"]),
      clone: one("clone", ["clonefile", "reflink", "unavailable", "path-bound", "not-used"]),
      verification: one("verification", ["index", "inventory"]),
      phases: phasesValue.map((entry) => {
        const phase = object(entry);
        if (!setupPhases.includes(phase["phase"]) || typeof phase["durationMs"] !== "number" || !Number.isSafeInteger(phase["durationMs"]) || phase["durationMs"] < 0) return corrupt2();
        return { phase: phase["phase"], durationMs: phase["durationMs"] };
      })
    };
  }
  return {
    availability: "available",
    phase: row["phase"],
    failure: safeFailure,
    command: commandDiagnostic(row["command"]),
    ...dependency === void 0 ? {} : { dependency },
    ...setup === void 0 ? {} : { setup }
  };
}
async function readScopedCheckDiagnostics(input) {
  const { rootDir, config, attemptIds } = input;
  if (!Array.isArray(attemptIds) || attemptIds.length > 100 || attemptIds.some((id) => typeof id !== "string" || !id.length || id.length > 128) || new Set(attemptIds).size !== attemptIds.length) throw new CheckSnapshotError("check_diagnostics_request_invalid", "Diagnostics require at most 100 unique nonempty attempt identifiers.");
  const requested = new Set(attemptIds), seen = /* @__PURE__ */ new Set();
  const providers = [];
  const storage = await resolveRecordStorage4(rootDir, { storageNamespace: config.storageNamespace, leaf: "scoped-attempts" });
  for (const provider of config.providers.filter((p) => p.check?.scope !== void 0)) {
    const rows = requested.size ? await new AttemptStore(path9.join(storage.storageDir, digestCanonical4({ gate: config.gateId, provider: provider.id }))).read() : [];
    if (rows.some((row) => row.attempt.providerId !== provider.id)) throw new CheckSnapshotError("check_attempt_corrupt", "Scoped history belongs to a different provider.");
    const attempts = [];
    for (const { attempt, payload } of rows.filter((row) => requested.has(row.attempt.attemptId))) {
      if (seen.has(attempt.attemptId)) throw new CheckSnapshotError("check_attempt_corrupt", "A requested attempt identifier is ambiguous.");
      seen.add(attempt.attemptId);
      const diagnostic = attempt.status === "running" ? { availability: "unavailable", reason: "running" } : payload?.diagnostic === void 0 ? { availability: "unavailable", reason: "legacy" } : projectDiagnostic(payload.diagnostic);
      attempts.push({ ...projectScopedAttempt(attempt, payload?.durationMs), diagnostic });
    }
    providers.push({ providerId: provider.id, attempts });
  }
  return { version: "scoped-check-diagnostics/1", providers, unavailableAttemptIds: attemptIds.filter((id) => !seen.has(id)) };
}

// packages/cli/src/scoped-checks.ts
function scopedCandidate(candidate) {
  return { treeSha: candidate.treeSha, deliverableDigest: candidate.deliverable.digest, identityToken: candidate.deliverable.identity, baseRef: candidate.base.ref, baseTipSha: candidate.base.tipSha, mergeBaseSha: candidate.base.mergeBaseSha, workspaceId: candidate.workspaceId };
}
var hostToolDigests = /* @__PURE__ */ new Map();
async function hostToolDigest(file, readBytes = readFile3) {
  const before = await stat2(file);
  const key = JSON.stringify([before.dev, before.ino, before.size, before.mtimeMs, before.ctimeMs]);
  let digest = hostToolDigests.get(key);
  if (!digest) {
    digest = readBytes(file).then((bytes) => sha256Hex3(bytes));
    hostToolDigests.set(key, digest);
    digest.catch(() => {
      if (hostToolDigests.get(key) === digest) hostToolDigests.delete(key);
    });
  }
  return { sha256: await digest, mode: before.mode & 73 };
}
async function executableIdentity(command, root, searchPath, cwd, read) {
  if (command.includes(path10.sep) && !path10.isAbsolute(command)) {
    const file = path10.posix.normalize(path10.posix.join(cwd, command));
    if (file === ".." || file.startsWith("../")) throw new CheckSnapshotError("check_runtime_unavailable", "A relative execution tool must stay inside the prepared tree.");
    const bytes = await read(file);
    if (bytes === null) throw new CheckSnapshotError("check_runtime_unavailable", "A relative execution tool is missing from the prepared tree.");
    return { command, sha256: sha256Hex3(bytes), metadata: await read.metadata(file) };
  }
  const candidates = command.includes(path10.sep) ? [path10.resolve(root, command)] : searchPath.split(path10.delimiter).map((p) => path10.join(p, command));
  for (const file of candidates) {
    try {
      await access(file);
    } catch {
      continue;
    }
    const relative = path10.relative(await realpath2(root), await realpath2(file));
    if (relative !== ".." && !relative.startsWith(`..${path10.sep}`) && !path10.isAbsolute(relative)) throw new CheckSnapshotError("check_runtime_author_path", "Repository execution tools must use a relative command so they run from the private prepared tree.");
    const { sha256, mode } = await hostToolDigest(file);
    return { command, sha256, mode };
  }
  throw new CheckSnapshotError("check_runtime_unavailable", "A declared execution tool cannot be resolved and bound.");
}
function setupKey(profile, physicalSource) {
  return digestCanonical5({ dependencies: profile.dependencies ?? null, dependencyInputs: [...profile.dependencyInputs].sort(), gitContext: profile.gitContext ?? "full", ...physicalSource ? { physicalSource } : {} });
}
function scopedProviders(context) {
  const providers = context.config.providers.filter((p) => p.check?.scope);
  if (providers.length && context.config.scopedExecution?.version !== "scoped-execution/1") throw new CheckSnapshotError("scoped_executor_required", "Scoped checks require supported private execution profiles before preparation.");
  return providers;
}
async function preparedSource(context, candidate) {
  const read = await candidateTreeSourceReader2(context.rootDir, candidate.treeSha);
  const listing = await runGitCommand(["git", "ls-tree", "-r", "--name-only", "-z", candidate.treeSha], { cwd: context.rootDir });
  if (listing.exitCode !== 0) throw new CheckSnapshotError("check_snapshot_unavailable", "Cannot enumerate the prepared source tree.");
  return { inventory: listing.stdout.split("\0").filter(Boolean), read };
}
function validateScopedProvider(context, provider, inventory) {
  const scope = provider.check.scope, profile = context.config.scopedExecution.profiles.find((p) => p.id === scope.profile);
  if (!profile) throw new CheckSnapshotError("scoped_executor_required", "A selected execution profile is unsupported.");
  if (profile.mutableOutputs.some((o) => inventory.some((f) => f === o.replace(/\/$/, "") || f.startsWith(`${o.replace(/\/$/, "")}/`)))) throw new CheckSnapshotError("check_output_overlaps_source", "Mutable outputs overlap prepared source.");
  if ((provider.check.outputs ?? []).some((o) => !profile.mutableOutputs.some((m) => m.endsWith("/") ? o.startsWith(m) : o === m))) throw new CheckSnapshotError("check_output_undeclared", "Every retained output must be declared mutable in its profile.");
  return profile;
}
var shownArgument = (argument) => /^[\w@%+=:,./-]+$/.test(argument) ? argument : JSON.stringify(argument);
function oneAtATime() {
  let tail = Promise.resolve();
  return (task) => {
    const result = tail.then(task);
    tail = result.catch(() => void 0);
    return result;
  };
}
function highestFirst() {
  const waiting = [];
  let busy = false;
  const next = () => {
    if (busy || waiting.length === 0) return;
    const index = waiting.reduce((best, entry, i) => entry.priority > waiting[best].priority ? i : best, 0);
    busy = true;
    waiting.splice(index, 1)[0].start();
  };
  return (priority, task) => new Promise((resolve, reject) => {
    waiting.push({ priority, start: () => {
      task().then(resolve, reject).finally(() => {
        busy = false;
        next();
      });
    } });
    next();
  });
}
var ScopedChecks = class _ScopedChecks {
  /** `scopedExecution.concurrency` workers, each owning its snapshots; the first is the serial lane. */
  workers;
  concurrency;
  phaseWriters = /* @__PURE__ */ new WeakMap();
  /** Attempt allocation is two writes, so readers of attempt history wait for it to finish. */
  locked = oneAtATime();
  /** Evidence submission captures the candidate through the author's Git index, which one writer may hold. */
  publishing = oneAtATime();
  /** Resource discovery is a pre-execution phase on the serial worker only. */
  capturing = oneAtATime();
  captureTail = Promise.resolve();
  capturePhaseOpen = true;
  cleanupStarted = false;
  /** With more than one worker: one read-only warm base per setup key, which no command runs in. Each
   * worker takes a copy-on-write clone of it; without cloning each worker builds its own, as before. */
  bases = /* @__PURE__ */ new Map();
  /** Each base's settlement, after which its entry is current: a failed setup has left `bases`. */
  settling = /* @__PURE__ */ new WeakMap();
  settled = /* @__PURE__ */ new WeakSet();
  /** Stops the bases `prewarm` started, which no attempt owns, once no check can need one still setting up. */
  prewarming = new AbortController();
  /** Prewarmed bases not yet taken as a worker's own tree, which a path-bound one becomes. */
  prewarmedBases = /* @__PURE__ */ new WeakSet();
  /** Concurrent copy-on-write clones of one large tree each take several times as long as one alone, so
   * they run one at a time, the check expected to run longest first. */
  cloningOne = highestFirst();
  cloning;
  /** Profiles whose checks a tree has run and verified since it was made: their outputs may be in it. */
  ranOutputs = /* @__PURE__ */ new WeakMap();
  executionLocks = /* @__PURE__ */ new Map();
  executionTails = /* @__PURE__ */ new Map();
  retainedResourceBindings;
  sourceTreeCache = /* @__PURE__ */ new Map();
  candidateSource;
  sourceFrames;
  installed = /* @__PURE__ */ new Map();
  owned = /* @__PURE__ */ new Map();
  stores = /* @__PURE__ */ new Map();
  identities = /* @__PURE__ */ new Map();
  executionIdentities = /* @__PURE__ */ new Map();
  observations = /* @__PURE__ */ new Map();
  runId = randomUUID6();
  submissions = [];
  context;
  candidate;
  constructor(context, candidate) {
    this.context = context;
    this.candidate = candidate;
    this.concurrency = context.config.scopedExecution?.concurrency ?? 1;
    this.workers = Array.from({ length: this.concurrency }, () => /* @__PURE__ */ new Map());
  }
  get serial() {
    return { snapshots: this.workers[0], write: (text2) => this.context.write(text2) };
  }
  /** Zero mechanical preparation validates the real narrow physical frame,
   * but does not hash broad consumer inputs or observe execution tools. Gate
   * creates its own fresh session; no deferred identity is retained here. */
  static async validatePreparation(context, candidate) {
    const providers = scopedProviders(context);
    if (!providers.length) return;
    const { inventory, read } = await preparedSource(context, candidate);
    const output = deliveryRecordPathFor2(context.config, candidate.deliverable.digest);
    const ordinaryInventory = inventory.filter((file) => file !== output), inventoryPaths = new Set(ordinaryInventory);
    for (const provider of providers) {
      const profile = validateScopedProvider(context, provider, inventory), scope = recordOutputProvider(provider, output).check.scope;
      const ordinarySelected = /* @__PURE__ */ new Set([
        ...scope.files,
        ...scope.tests,
        ...scope.memberships.flatMap((prefix) => ordinaryInventory.filter((file) => file.startsWith(prefix)))
      ]);
      const selected = /* @__PURE__ */ new Set([...ordinarySelected, ...profile.dependencyInputs]);
      for (const file of selected) {
        const metadata = await read.metadata(file);
        if (metadata.mode === null) {
          if (profile.dependencyInputs.includes(file)) throw new CheckSnapshotError("check_dependency_input_missing", `Required dependency input ${file} is missing from the prepared tree.`);
          if (scope.tests.includes(file)) throw new Error("Required test is absent");
        }
        if (ordinarySelected.has(file) && metadata.mode !== null !== inventoryPaths.has(file))
          throw new Error("Snapshot membership and metadata presence disagree");
      }
      if (provider.check.physicalSource) {
        try {
          await capturePhysicalSource2(provider.check.physicalSource, read);
        } catch {
          throw new CheckSnapshotError("check_physical_source_invalid", "Physical source bytes, modes or required absences are unavailable or unsafe.");
        }
      }
    }
  }
  static async create(context, candidate) {
    const providers = scopedProviders(context);
    if (!providers.length) return void 0;
    void sweepRemovedTrees().catch(() => void 0);
    const session = new _ScopedChecks(context, candidate);
    const storage = await resolveRecordStorage5(context.rootDir, { storageNamespace: context.config.storageNamespace, leaf: "scoped-attempts" });
    const { inventory, read } = await preparedSource(context, candidate);
    const readEvidence = await candidateTreeEvidenceReader(context.rootDir, candidate.treeSha);
    session.candidateSource = { inventory, read };
    const validationConfig = { ...context.config, computingIdentityVersion: "validation-tree/v1", reviewNeutral: context.config.recordNeutral };
    const validationTrees = /* @__PURE__ */ new Map();
    const validation = (treeSha) => {
      let digest = validationTrees.get(treeSha);
      if (!digest) {
        digest = computeDeliverableIdentity({ rootDir: context.rootDir, treeSha, config: validationConfig });
        validationTrees.set(treeSha, digest);
      }
      return digest;
    };
    for (const provider of providers) {
      const scope = provider.check.scope, profile = validateScopedProvider(context, provider, inventory);
      const searchPath = executionPath(context.rootDir, context.env["PATH"] ?? process.env["PATH"] ?? "/usr/bin:/bin");
      const tools = await Promise.all([[process.execPath, "."], [provider.check.command[0], scope.cwd], ...profile.dependencies ? [[profile.dependencies.command[0], "."]] : []].map(([c, cwd]) => executableIdentity(c, context.rootDir, searchPath, cwd, read)));
      const observation = {
        version: "scoped-runtime/1",
        runtimeDigest: digestCanonical5({ platform: process.platform, arch: process.arch, tools, searchPath }),
        flags: Object.fromEntries(scope.environment.filter((e) => e.kind === "flag" && context.env[e.name] !== void 0).map((e) => [e.name, context.env[e.name]])),
        credentials: Object.fromEntries(scope.environment.filter((e) => e.kind === "credential").map((e) => {
          const present = context.env[e.name] !== void 0, identity2 = present ? profile.credentialIdentities[e.name] ?? null : null;
          if (identity2 !== null && (identity2 === context.env[e.name] || identity2 === sha256Hex3(context.env[e.name] ?? ""))) throw new CheckSnapshotError("check_credential_identity_invalid", "Credential identity must be an external nonsecret revision.");
          return [e.name, { present, identity: identity2 }];
        }))
      };
      session.observations.set(provider.id, observation);
      const store = new AttemptStore(path10.join(storage.storageDir, digestCanonical5({ gate: context.config.gateId, provider: provider.id })));
      session.stores.set(provider.id, store);
      let identity = await scopedCheckIdentity(context.config, provider, inventory, read, observation, candidate, readEvidence);
      session.executionIdentities.set(provider.id, identity);
      const history = await store.read();
      if (identity.reusable && profile.gitContext !== "none" && !history.some(({ attempt }) => attempt.inputDigest === identity.inputDigest && attempt.profileDigest === identity.profileDigest)) {
        for (const { attempt } of history.reverse()) {
          const origin = attempt.origin.candidate;
          if (origin.treeSha === candidate.treeSha || origin.baseRef !== candidate.base.ref || origin.baseTipSha !== candidate.base.tipSha || origin.mergeBaseSha !== candidate.base.mergeBaseSha || origin.workspaceId !== candidate.workspaceId || origin.identityToken !== candidate.deliverable.identity || await validation(origin.treeSha) !== await validation(candidate.treeSha)) continue;
          const transported = await scopedCheckIdentity(context.config, provider, inventory, read, observation, { ...candidate, treeSha: origin.treeSha }, readEvidence);
          if (transported.inputDigest === attempt.inputDigest && transported.profileDigest === attempt.profileDigest) {
            identity = transported;
            break;
          }
        }
      }
      session.identities.set(provider.id, identity);
    }
    return session;
  }
  async plan() {
    const checks = {};
    for (const [id, identity] of this.identities) checks[id] = { inputDigest: identity.inputDigest, profileDigest: identity.profileDigest, reusable: identity.reusable, attempts: (await this.locked(() => this.stores.get(id).read())).map((r) => r.attempt) };
    return { version: "scoped-plan/1", candidate: scopedCandidate(this.candidate), selectionDigest: digestCanonical5(this.context.config.providers.filter((p) => p.check?.scope).map((p) => ({ id: p.id, check: p.check })).sort((a, b) => a.id.localeCompare(b.id))), checks };
  }
  async selected(id) {
    const identity = this.identities.get(id);
    const rows = await this.locked(() => this.stores.get(id).read());
    const selected = selectScopedCheckAttempt(id, identity.inputDigest, identity.profileDigest, rows.map((r) => r.attempt));
    return rows.find((r) => r.attempt.attemptId === selected?.attemptId);
  }
  readOutput = async (repoPath, providerId) => {
    if (!this.identities.has(providerId)) return readFile3(path10.join(this.context.rootDir, repoPath));
    const selected = await this.selected(providerId);
    const output = selected?.attempt.status === "passed" ? selected.payload?.outputs.find((o) => o.path === repoPath) : void 0;
    if (!output || sha256Hex3(Buffer.from(output.base64, "base64")) !== output.sha256) throw new CheckSnapshotError("check_output_missing", "Retained scoped output is absent or corrupt.");
    return Buffer.from(output.base64, "base64");
  };
  /** No safe secret identity means a fresh attempt in every executing invocation. */
  async fenceNonReusable(ids) {
    for (const id of ids) if (!this.identities.get(id).reusable && !this.owned.has(id)) await this.allocate(id);
  }
  async allocate(id) {
    const identity = this.executionIdentities.get(id);
    this.identities.set(id, identity);
    const attempt = await this.locked(() => this.stores.get(id).allocate({ version: "scoped-attempt/1", providerId: id, inputDigest: identity.inputDigest, profileDigest: identity.profileDigest, origin: { candidate: scopedCandidate(this.candidate), runId: this.runId } }, { executionHeadSha: this.candidate.headSha }));
    this.owned.set(id, attempt);
    return attempt;
  }
  async explainReuse(admitted) {
    for (const id of this.identities.keys()) {
      const selected = await this.selected(id);
      if (admitted.includes(id) && selected?.attempt.status === "passed") this.context.write(`reusing ${id}: matching inputs/profile; attempt ${selected.attempt.attemptId}`);
      for (const row of await this.stores.get(id).read()) if (row.attempt.status !== "passed" && row.attempt.inputDigest !== this.identities.get(id).inputDigest) this.context.write(`superseded ${id}: ${row.attempt.status} attempt ${row.attempt.attemptId} has different inputs`);
    }
  }
  profileOf(id) {
    const scope = this.context.config.providers.find((p) => p.id === id).check.scope;
    return this.context.config.scopedExecution.profiles.find((p) => p.id === scope.profile);
  }
  /** The first declared check runs first: it is where an adopter puts a selection guard that must precede
   * every other check. The rest run cheapest first, each costing its most recent passed or failed attempt's
   * duration less that attempt's own dependency setup; never-timed checks follow timed ones and ties keep
   * declaration order. Checks then run one dependency setup at a time, in the order each setup first
   * appears, so each setup installs once. */
  /** A check's most recent passed or failed attempt's duration less that attempt's own dependency setup, any
   * wait for another's warm base, and its clone with that clone's queue; undefined for a never-timed check. */
  async lastCost(id) {
    const last = (await this.stores.get(id).read()).filter((r) => r.attempt.status === "passed" || r.attempt.status === "failed").at(-1)?.payload;
    const waited = last?.diagnostic?.setup?.phases.filter((p) => p.phase === "warm-base-wait" || p.phase === "clone").reduce((sum, p) => sum + p.durationMs, 0) ?? 0;
    return last?.durationMs === void 0 ? void 0 : last.durationMs - (last.diagnostic?.dependency?.durationMs ?? 0) - waited;
  }
  /** How long each check is expected to run: its last cost, or, never timed, its declared `timeoutMs`. */
  async expectedCosts(ids) {
    const costs = /* @__PURE__ */ new Map();
    for (const id of ids) costs.set(id, await this.lastCost(id) ?? this.context.config.providers.find((p) => p.id === id)?.check?.timeoutMs ?? 0);
    return costs;
  }
  async cheapestFirst(ids) {
    const cost = /* @__PURE__ */ new Map();
    for (const id of ids) cost.set(id, await this.lastCost(id) ?? Infinity);
    const [first, ...rest] = ids;
    if (first === void 0) return [];
    const byCost = [first, ...rest.sort((a, b) => cost.get(a) === cost.get(b) ? 0 : cost.get(a) < cost.get(b) ? -1 : 1)];
    const keys = byCost.map((id) => setupKey(this.profileOf(id), this.context.config.providers.find((p) => p.id === id)?.check?.physicalSource));
    return [...new Set(keys)].flatMap((key) => byCost.filter((_, index) => keys[index] === key));
  }
  /**
   * Runs ids on the session's workers, each taking the next id when it is free. Every worker keeps its own
   * snapshots, so two checks never share one at once. A worker prints a check's lines together when it
   * finishes; a single worker prints them as they come. After a check throws no new id starts, the running
   * ones finish, and the first failure in `ids` order is thrown.
   *
   * A worker takes the first remaining id whose warm base is not still setting up, so no check's setup waits
   * on a base being built; while every remaining id's base is, the worker waits for one to settle before
   * taking a check. Once `halted`, the remaining ids are taken in order without waiting. With `expected`, a
   * worker takes the startable id expected to run longest instead, and its clone queues by that cost, so
   * the critical path gets its base, clone and worker first.
   */
  async pool(ids, run, halted = () => false, expected, onSaturated) {
    let busy = 0, saturated = false;
    const saturate = () => {
      if (!saturated) {
        saturated = true;
        onSaturated?.();
      }
    };
    if (ids.length === 0) saturate();
    const failures = /* @__PURE__ */ new Map(), remaining = [...ids];
    const sleepers = /* @__PURE__ */ new Set();
    const woken = () => new Promise((resolve) => {
      sleepers.add(resolve);
    });
    const waker = () => {
      for (const sleeper of [...sleepers]) sleeper();
      sleepers.clear();
    };
    this.poolWakers.add(waker);
    const take = async () => {
      for (; ; ) {
        if (failures.size > 0 || remaining.length === 0) return void 0;
        const index = halted() ? 0 : remaining.reduce((best, id, i) => !this.startable(id) ? best : best < 0 || (expected?.get(id) ?? 0) > (expected?.get(remaining[best]) ?? 0) ? i : best, -1);
        if (index >= 0) {
          const id = remaining.splice(index, 1)[0];
          this.claim(id);
          busy += 1;
          if (remaining.length === 0 || busy >= this.workers.length) saturate();
          return id;
        }
        await Promise.race([woken(), ...remaining.flatMap((id) => {
          const key = this.keyOf(id), base = this.bases.get(key);
          return base ? [this.settling.get(base)] : this.claims.has(key) ? [this.claims.get(key).released] : [];
        })]);
      }
    };
    try {
      await Promise.all(this.workers.map(async (snapshots) => {
        for (let id = await take(); id !== void 0; id = await take()) {
          const lines = [];
          try {
            await run(id, this.concurrency === 1 ? this.serial : { snapshots, write: (text2) => {
              lines.push(text2);
            }, priority: expected?.get(id) ?? 0 });
          } catch (error) {
            failures.set(id, error);
            this.wakePools();
          } finally {
            busy -= 1;
            this.release(this.keyOf(id), id);
            for (const line of lines) this.context.write(line);
          }
        }
      }));
    } finally {
      this.poolWakers.delete(waker);
    }
    const failed = ids.find((id) => failures.has(id));
    if (failed !== void 0) throw failures.get(failed);
  }
  poolWakers = /* @__PURE__ */ new Set();
  wakePools() {
    for (const wake of [...this.poolWakers]) wake();
  }
  keyOf(id) {
    return setupKey(this.profileOf(id), this.context.config.providers.find((p) => p.id === id)?.check?.physicalSource);
  }
  /** A base for this id's setup key whose setup has settled, or no base and no check about to set one up. */
  startable(id) {
    const key = this.keyOf(id), base = this.bases.get(key);
    return base === void 0 ? !this.claims.has(key) : this.settled.has(base);
  }
  /** While bases are shared, a check taken for a key with no base holds the key until its setup has started
   * one (or it finished), so a second check of that key waits outside its attempt rather than on that build. */
  claims = /* @__PURE__ */ new Map();
  sharing = false;
  claim(id) {
    const key = this.keyOf(id);
    if (!this.sharing || this.bases.has(key) || this.claims.has(key)) return;
    let release = () => {
    };
    const released = new Promise((resolve) => {
      release = resolve;
    });
    this.claims.set(key, { owner: id, released, release });
  }
  /** Ends the key's claim: once a base exists, whoever holds it; when a check finishes, only its own. */
  release(key, owner) {
    const claim = this.claims.get(key);
    if (!claim || owner !== void 0 && claim.owner !== owner) return;
    this.claims.delete(key);
    claim.release();
  }
  /** Stops at the first failure: a failed mechanical check publishes no receipt. */
  async satisfyMechanical() {
    const ids = await this.cheapestFirst(this.context.config.scopedExecution.mechanicalProviders);
    await this.fenceNonReusable(ids);
    const satisfy = async (id, lane) => {
      if ((await this.selected(id))?.attempt.status === "passed") {
        lane.write(`reusing mechanical ${id}`);
        return;
      }
      const provider = this.context.config.providers.find((p) => p.id === id);
      await this.execute(provider, this.context.config.obligations.filter((o) => o.providers.includes(id)).map((o) => o.id), true, lane);
    };
    const executing = async (some) => {
      const out = [];
      for (const id of some) if ((await this.selected(id))?.attempt.status !== "passed") out.push(id);
      return out;
    };
    await this.prewarm(await executing(ids.slice(0, 1)));
    await this.pool(ids.slice(0, 1), satisfy);
    await this.prewarm(await executing(ids.slice(1)));
    await this.pool(ids.slice(1), satisfy);
  }
  /**
   * Runs the gate's requested checks on the pool. A check's own failure is returned in request order. Without
   * `scopedExecution.failFast` it stops nothing; with it, the first failure cancels every other check: one not
   * yet started never starts and one running has its process group signalled, and both are recorded
   * `interrupted` with `check_cancelled` and left out of the returned failures. `onFailFast` hears that first
   * failure. `onAllStarted` hears, once, that the pool is saturated: every worker holds a check, or every
   * requested check has been handed out (at once when none remains for the pool). Not at all if the pool throws.
   */
  async executeAll(requests, onFailFast, onAllStarted) {
    const failures = /* @__PURE__ */ new Map();
    const failFast = this.context.config.scopedExecution?.failFast === true;
    const running = /* @__PURE__ */ new Map(), unstarted = [];
    let firstFailure;
    const run = async (id, lane) => {
      if (firstFailure !== void 0) {
        unstarted.push(id);
        return;
      }
      const request = requests.find((r) => r.provider.id === id);
      const cancel = failFast ? new AbortController() : void 0;
      if (cancel) running.set(id, cancel);
      try {
        await this.execute(request.provider, request.obligationIds, false, lane, cancel && { signal: cancel.signal, after: () => firstFailure });
      } catch (error) {
        if (!(error instanceof CheckSnapshotError)) throw error;
        if (error.code === "check_cancelled") return;
        failures.set(id, error);
        if (failFast && firstFailure === void 0) {
          firstFailure = id;
          onFailFast?.(id);
          for (const [other, controller] of running) if (other !== id) controller.abort();
          this.wakePools();
        }
      } finally {
        running.delete(id);
      }
    };
    const halted = () => firstFailure !== void 0;
    const expected = await this.expectedCosts(requests.map((r) => r.provider.id));
    const resource = requests.find((r) => this.acceptsApplicability(r.provider, r.obligationIds));
    const serialIds = resource ? [.../* @__PURE__ */ new Set([requests[0].provider.id, resource.provider.id])] : [];
    await this.prewarm(requests.map((r) => r.provider.id), expected, serialIds);
    if (resource) {
      for (const id of serialIds) await run(id, this.serial);
      await this.pool(requests.map((r) => r.provider.id).filter((id) => !serialIds.includes(id)), run, halted, expected, onAllStarted);
    } else await this.pool(requests.map((r) => r.provider.id), run, halted, expected, onAllStarted);
    if (firstFailure !== void 0) await this.cancelUnstarted(unstarted, firstFailure);
    return requests.flatMap((r) => failures.has(r.provider.id) ? [failures.get(r.provider.id)] : []);
  }
  /** Records each check a fail-fast gate never started as `interrupted`, with `check_cancelled` and no command. */
  async cancelUnstarted(ids, after) {
    for (const id of ids) {
      const attempt = this.owned.get(id) ?? await this.allocate(id), store = this.stores.get(id);
      await store.finish(attempt, "interrupted", {
        outputs: [],
        durationMs: 0,
        diagnostic: { availability: "available", phase: "snapshot-setup", failure: { code: "check_cancelled" }, command: { unavailable: "not-started" } },
        log: "check_cancelled"
      });
      this.context.write(`interrupted ${id}: check_cancelled before it started, after ${after} failed; attempt ${attempt.attemptId} retained at ${store.terminalPath(attempt)}`);
    }
  }
  async submitMechanical() {
    for (const submit of this.submissions) await submit();
    this.submissions.length = 0;
  }
  /** One acquisition path preserves setup sharing, output ownership and worker isolation. A worker's tree
   * for a setup key is built here, cloned from the warm base, or retained from its previous check.
   * `onSetup` hears how the tree is being got before any setup runs, so a failed setup is attributed. */
  /** Every scoped check's profile sharing this setup may write its own outputs in the shared tree;
   * only those profiles passed the overlap refusal in create(). */
  sharedOutputsFor(key) {
    return [...new Set(this.context.config.scopedExecution.profiles.filter((p) => this.context.config.providers.some((q) => q.check?.scope?.profile === p.id && setupKey(p, q.check.physicalSource) === key)).flatMap((p) => p.mutableOutputs))];
  }
  buildSnapshot(profile, physicalSource, onPhase, dependencyResult, signal = this.context.signal) {
    return createCheckSnapshot({
      rootDir: this.context.rootDir,
      candidate: this.candidate,
      outputs: this.sharedOutputsFor(setupKey(profile, physicalSource)),
      gitContext: profile.gitContext ?? "full",
      environment: { PATH: this.context.env["PATH"] ?? process.env["PATH"] ?? "/usr/bin:/bin" },
      ...physicalSource ? { physicalSource } : {},
      ...profile.dependencies ? { dependencies: profile.dependencies } : {},
      ...signal ? { signal } : {},
      onPhase,
      ...dependencyResult ? { onDependencyResult: dependencyResult } : {}
    });
  }
  /** Shares `base` for its setup key. A failed setup is not shared: it leaves `bases` as it settles, and the
   * next check of this key sets up again, as it would on its own worker. */
  shareBase(key, base) {
    this.bases.set(key, base);
    this.settling.set(base, base.then(() => {
      this.settled.add(base);
    }, () => {
      this.settled.add(base);
      if (this.bases.get(key) === base) this.bases.delete(key);
    }));
  }
  /**
   * Sets up the warm base of each setup key among `ids` that has none yet, so the checks that clone it neither
   * build it nor wait for another check to. Bases set up one at a time: concurrent dependency installs on one
   * machine each take about twice as long, so a key's base is ready soonest on its own. With `expected`, the
   * keys of `leading` go first, in that order, and then the key whose own critical path is longest: its last
   * recorded build plus the longest expected check that needs it. Without it, the order of `ids`. Every key
   * is registered at once, so no worker takes its checks before its base settles. No attempt owns this setup:
   * a check cloning the base records it as reused and carries no dependency result, and a failed one leaves
   * no base, so the check that next needs its key sets up itself and owns that result. Only with cloning:
   * without it each worker builds its own tree, as before.
   */
  async prewarm(ids, expected, leading = []) {
    if (this.concurrency === 1 || !await (this.cloning ??= snapshotCloneMethod())) return;
    this.sharing = true;
    const signal = this.context.signal ? AbortSignal.any([this.context.signal, this.prewarming.signal]) : this.prewarming.signal;
    const keyed = /* @__PURE__ */ new Map();
    for (const [order, id] of ids.entries()) {
      const key = this.keyOf(id), check = expected?.get(id) ?? 0, seen = keyed.get(key);
      if (this.bases.has(key)) continue;
      if (!seen) keyed.set(key, { id, check, order });
      else if (check > seen.check) keyed.set(key, { id, check, order: seen.order });
    }
    const rank = (key) => {
      const index = leading.findIndex((id) => this.keyOf(id) === key);
      return index < 0 ? leading.length : index;
    };
    const critical = /* @__PURE__ */ new Map();
    for (const [key, { check }] of keyed) critical.set(key, expected ? await this.lastBuild(key) + check : 0);
    const keys = [...keyed].sort(([a, x], [b, y]) => rank(a) - rank(b) || critical.get(b) - critical.get(a) || x.order - y.order);
    for (const [key, { id }] of keys) {
      const profile = this.profileOf(id), physicalSource = this.context.config.providers.find((p) => p.id === id)?.check?.physicalSource;
      const previous = this.prewarmTail;
      const base = (async () => {
        await previous;
        const started = performance.now();
        const snapshot = await this.buildSnapshot(profile, physicalSource, (phase, durationMs) => this.context.write(`prewarm ${profile.id}: ${phase} ${Math.round(durationMs)}ms`), void 0, signal);
        this.timingWrites.push(this.recordBuild(key, performance.now() - started));
        return snapshot;
      })();
      this.prewarmTail = base.then(() => void 0, () => void 0);
      this.prewarmedBases.add(base);
      this.shareBase(key, base);
      base.catch((error) => {
        if (!this.prewarming.signal.aborted) this.context.write(`prewarm ${profile.id}: setup failed (${error instanceof CheckSnapshotError ? error.code : "unclassified"}); its first check sets up itself`);
      });
    }
  }
  /** Where the last successful prewarm of each setup key's base is timed, resolved once a session. Ordering
   * only: no admission, gate or record reads it, and a missing or unreadable timing counts as no build time.
   * A check-owned build is not timed: it follows a failed prewarm and may share the machine with the chain. */
  timings;
  timingWrites = [];
  async timingPath(key) {
    const dir = await (this.timings ??= resolveRecordStorage5(this.context.rootDir, { storageNamespace: this.context.config.storageNamespace, leaf: "scoped-setup-timings" }).then((storage) => storage.storageDir));
    return path10.join(dir, `${digestCanonical5({ gate: this.context.config.gateId, setup: key })}.json`);
  }
  async lastBuild(key) {
    try {
      const timing = JSON.parse(await readFile3(await this.timingPath(key), "utf8"));
      return timing.version === "scoped-setup-timing/1" && typeof timing.buildMs === "number" && Number.isFinite(timing.buildMs) && timing.buildMs >= 0 ? timing.buildMs : 0;
    } catch {
      return 0;
    }
  }
  async recordBuild(key, buildMs) {
    let temporary;
    try {
      const file = await this.timingPath(key);
      temporary = `${file}.${randomUUID6()}`;
      await mkdir4(path10.dirname(file), { recursive: true, mode: 448 });
      await writeFile4(temporary, `${JSON.stringify({ version: "scoped-setup-timing/1", buildMs: Math.round(buildMs) })}
`);
      await rename2(temporary, file);
    } catch {
      if (temporary) await rm3(temporary, { force: true }).catch(() => void 0);
    }
  }
  prewarmTail = Promise.resolve();
  async acquireSnapshot(profile, lane, onDependencyResult, physicalSource, onSetup = () => {
  }) {
    const key = setupKey(profile, physicalSource);
    const sharedOutputs = this.sharedOutputsFor(key);
    const method = this.concurrency === 1 ? void 0 : await (this.cloning ??= snapshotCloneMethod());
    const acquired = (snapshot, setup) => {
      lane.snapshots.set(key, snapshot);
      onSetup(setup);
      return { snapshot, sharedOutputs, setup };
    };
    const retained = lane.snapshots.get(key);
    if (retained) return acquired(retained, { action: "reused", snapshot: "retained", clone: this.concurrency === 1 ? "not-used" : method ?? "unavailable" });
    if (this.concurrency === 1) for (const [previousKey, previous] of lane.snapshots) {
      await previous.cleanup();
      lane.snapshots.delete(previousKey);
    }
    const onPhase = (phase, durationMs) => {
      const write = this.phaseWriters.get(lane.snapshots);
      if (write) write(phase, durationMs);
      else lane.write(`snapshot ${profile.id}: bootstrap ${phase} ${Math.round(durationMs)}ms`);
    };
    const build = (dependencyResult) => this.buildSnapshot(profile, physicalSource, onPhase, dependencyResult);
    let executed = false, clone = this.concurrency === 1 ? "not-used" : "unavailable";
    if (method) {
      let base = this.bases.get(key);
      if (!base) {
        executed = true;
        base = build(onDependencyResult);
        this.shareBase(key, base);
      }
      this.release(key);
      onSetup({ action: executed ? "executed" : "reused", snapshot: "cloned", clone: method });
      const waiting = performance.now();
      let warm;
      try {
        warm = await base;
      } catch (error) {
        if (executed || !(error instanceof CheckSnapshotError) || this.context.signal?.aborted) throw error;
        lane.write(`snapshot ${profile.id}: shared setup failed (${error.code}); setting up again`);
      }
      if (!executed) onPhase("warm-base-wait", performance.now() - waiting);
      if (warm && !warm.clonable) {
        clone = "path-bound";
        lane.write(`snapshot ${profile.id}: setup wrote its own path into the tree; each worker builds its own`);
        if (executed) return acquired(warm, { action: "executed", snapshot: "built", clone });
        if (this.prewarmedBases.delete(base)) return acquired(warm, { action: "reused", snapshot: "built", clone });
      } else if (warm) {
        const queued = performance.now(), source = warm;
        const cloned = () => this.cloningOne(lane.priority ?? 0, () => {
          const wait = performance.now() - queued;
          return source.clone(method, (phase, durationMs) => onPhase(phase, phase === "clone" ? durationMs + wait : durationMs));
        });
        try {
          return acquired(await cloned(), { action: executed ? "executed" : "reused", snapshot: "cloned", clone: method });
        } catch (error) {
          if (!(error instanceof CheckSnapshotError) || this.context.signal?.aborted) throw error;
          lane.write(`snapshot ${profile.id}: clone unavailable; ${error.code}`);
        }
      }
    }
    onSetup({ action: "executed", snapshot: "built", clone });
    return acquired(await build(executed ? void 0 : onDependencyResult), { action: "executed", snapshot: "built", clone });
  }
  scheduleCapture(task, executionStage = false) {
    if (this.cleanupStarted || !this.capturePhaseOpen && !executionStage) return Promise.reject(new CheckSnapshotError("installed_data_capture_closed", "Resource capture must finish before execution or cleanup begins."));
    const accepted = this.capturing(task);
    this.captureTail = accepted.catch(() => void 0);
    return accepted;
  }
  sourceTree(sha) {
    if (sha === this.candidate.treeSha) return Promise.resolve(this.candidateSource);
    let tree = this.sourceTreeCache.get(sha);
    if (!tree) {
      tree = (async () => {
        const listing = await runGitCommand(["git", "ls-tree", "-r", "--name-only", "-z", sha], { cwd: this.context.rootDir });
        if (listing.exitCode !== 0) throw new CheckSnapshotError("installed_data_source_unqualified", "Cannot enumerate installed resource source inputs.");
        return { inventory: listing.stdout.split("\0").filter(Boolean), read: await candidateTreeSourceReader2(this.context.rootDir, sha) };
      })();
      this.sourceTreeCache.set(sha, tree);
    }
    return tree;
  }
  async sourceTrees() {
    return this.sourceFrames ??= (async () => ({ base: await this.sourceTree(this.candidate.base.mergeBaseSha), candidate: this.candidateSource }))();
  }
  async captureResourceFacts(providerId, requests, context, lane = this.serial) {
    const provider = this.context.config.providers.find((p) => p.id === providerId);
    const profile = this.context.config.scopedExecution?.profiles.find((p) => p.id === provider?.check?.scope?.profile);
    if (!provider?.check?.scope || !profile || !this.identities.has(providerId)) throw new CheckSnapshotError("scoped_executor_required", "Installed resources require an already configured scoped provider and profile.");
    const normalized = normalizeInstalledDataResourceRequests2(requests);
    try {
      const { snapshot, sharedOutputs } = await this.acquireSnapshot(profile, lane, void 0, provider.check.physicalSource);
      for (const output of sharedOutputs) await rm3(path10.join(snapshot.rootDir, output), { recursive: true, force: true });
      const capture2 = await captureOwnedInstalledDataResources(snapshot, normalized, context, this.context.signal ? { signal: this.context.signal } : {});
      return { capture: capture2, snapshot };
    } catch (error) {
      const key = setupKey(profile, provider.check.physicalSource), damaged = lane.snapshots.get(key);
      await damaged?.cleanup();
      lane.snapshots.delete(key);
      throw error;
    }
  }
  /** Native resource facts only: caller-supplied context never seeds applicability. */
  captureInstalledData(providerId, requests, context) {
    return this.scheduleCapture(async () => (await this.captureResourceFacts(providerId, requests, context)).capture);
  }
  /** Derive authority from the configured consumer and immutable native source frames.
   * Matching resources do not make the prior full-Git check attempt current. */
  captureInstalledDataFor(providerId) {
    return this.scheduleCapture(() => this.captureDeclaredInstalledData(providerId));
  }
  async captureDeclaredInstalledData(providerId, lane = this.serial) {
    const provider = this.context.config.providers.find((p) => p.id === providerId);
    if (!provider?.check?.installedData || !this.observations.has(providerId)) throw new CheckSnapshotError("installed_data_source_unqualified", "A configured installed data consumer is required.");
    const binding = await captureInstalledDataBinding(this.context.config, provider, this.candidate, this.observations.get(providerId), await this.sourceTrees(), await candidateTreeEvidenceReader(this.context.rootDir, this.candidate.treeSha));
    const { capture: capture2, snapshot } = await this.captureResourceFacts(providerId, binding.declaration.requests, binding.context, lane);
    this.installed.set(providerId, { binding: structuredClone(binding), capture: structuredClone(capture2), snapshot });
    const latest = (await this.locked(() => this.stores.get(providerId).read())).at(-1);
    const origin = latest && this.resourceOrigin(latest);
    const match = matchResourceApplicabilityProjection(latest?.payload?.resourceApplicability, { consumerId: binding.declaration.consumerId, declarationDigest: binding.declarationDigest, sourceApplicabilityDigest: binding.sourceApplicabilityDigest, capture: capture2, originAttempt: origin ?? null });
    return { capture: capture2, ...match };
  }
  /** Reuse only an already accepted same-candidate applicability observation.
   * New source candidates still require fresh native installed-resource capture. */
  async acceptedResourceBindings() {
    return this.retainedResourceBindings ??= (async () => {
      const bindings = {};
      const wiring = await this.context.wire();
      for (const obligation of this.context.config.obligations.filter((o) => o.acceptedPayloadSpecs.includes("installed-data.applicable/1"))) {
        const found = await discoverRecords(this.context.rootDir, { ...wiring.storageOptions, gateId: this.context.config.gateId, obligationId: obligation.id });
        for (const record4 of found.records) {
          if (record4.resolution.kind !== "evidence" || !record4.resolution.resourceBinding || !record4.resolution.portable || !isRecordFreshForCandidate(this.context.config, record4.candidateBinding, this.candidate)) continue;
          const id = record4.resolution.providerId, provider = this.context.config.providers.find((p) => p.id === id);
          if (!provider?.check?.installedData || !this.observations.has(id)) continue;
          try {
            if (record4.candidateBinding.treeSha !== this.candidate.treeSha) {
              const diff = await runGitCommand(["git", "diff", "--name-only", "--no-renames", "-z", record4.candidateBinding.treeSha, this.candidate.treeSha], { cwd: this.context.rootDir });
              if (diff.exitCode !== 0 || diff.stdout.split("\0").filter(Boolean).some((file) => !isRecordNeutralPath(this.context.config, file))) continue;
            }
            const contents = portableArtifactContents(record4.resolution.portable.artifacts);
            if (contents.blockers.length) continue;
            const proof = JSON.parse(contents.artifacts.get("resource-proof.json") ?? "null");
            if (digestCanonical5(proof?.observation) !== digestCanonical5(this.observations.get(id))) continue;
            const reconstructed = await reconstructResourceApplicability(this.context.config, provider, { ...this.candidate, treeSha: record4.candidateBinding.treeSha }, proof, (sha) => this.sourceTree(sha), await candidateTreeEvidenceReader(this.context.rootDir, this.candidate.treeSha));
            if (digestCanonical5(reconstructed.binding) === digestCanonical5(record4.resolution.resourceBinding)) bindings[id] = reconstructed.binding;
          } catch {
          }
        }
      }
      return bindings;
    })();
  }
  /** Current native origin precedence is rechecked on each admission/submission. */
  async resourceBindings() {
    const bindings = { ...await this.acceptedResourceBindings() };
    for (const id of Object.keys(bindings)) {
      const latest = (await this.locked(() => this.stores.get(id).read())).at(-1);
      const origin = latest && this.resourceOrigin(latest);
      if (!origin || !latest?.payload?.resourceApplicability || digestCanonical5(origin) !== bindings[id].originDigest) delete bindings[id];
    }
    for (const [id, current] of this.installed) {
      const latest = (await this.locked(() => this.stores.get(id).read())).at(-1);
      const origin = latest && this.resourceOrigin(latest), projection = latest?.payload?.resourceApplicability;
      if (!origin || !projection) continue;
      const { binding, capture: capture2 } = current;
      if (!matchResourceApplicabilityProjection(projection, { consumerId: binding.declaration.consumerId, declarationDigest: binding.declarationDigest, sourceApplicabilityDigest: binding.sourceApplicabilityDigest, capture: capture2, originAttempt: origin }).applicable) continue;
      const provider = this.context.config.providers.find((p) => p.id === id);
      bindings[id] = createResourceApplicabilityBinding({
        candidateDigest: digestCanonical5(scopedCandidate(this.candidate)),
        definitionDigest: digestCanonical5(recordOutputProvider(provider, deliveryRecordPathFor2(this.context.config, this.candidate.deliverable.digest)).check),
        declarationDigest: binding.declarationDigest,
        sourceApplicabilityDigest: binding.sourceApplicabilityDigest,
        capture: capture2,
        projection,
        originAttempt: origin
      });
    }
    return bindings;
  }
  /** Publish a current applicability outcome, with no allocation or consumer pass. */
  async publishInstalledDataApplicability(providerId, obligationIds) {
    const current = this.installed.get(providerId);
    if (!current) throw new CheckSnapshotError("installed_data_source_unqualified", "Native resource capture is required before applicability publication.");
    await current.snapshot.verify({ outputs: this.profileOf(providerId).mutableOutputs });
    const binding = (await this.resourceBindings())[providerId];
    const origin = (await this.locked(() => this.stores.get(providerId).read())).at(-1);
    if (!binding || !origin?.payload?.resourceApplicability || digestCanonical5(origin.payload.resourceApplicability.origin) !== binding.originDigest)
      throw new CheckSnapshotError("check_attempt_superseded", "Native resource origin changed before applicability publication.");
    const runId = randomUUID6(), finalPassId = "applicability-1", c = this.candidate;
    const allocation = await this.context.artifacts.allocateRunRoot({ providerId, runId });
    if (!allocation.ok) throw new CheckSnapshotError("check_artifact_unavailable", "Cannot allocate resource applicability evidence.");
    const artifacts = [];
    const write = async (name, value2, role) => {
      const text2 = JSON.stringify(value2);
      await this.context.artifacts.writeTextFile(path10.join(allocation.runRoot.path, name), text2);
      artifacts.push({ path: name, sha256: sha256Hex3(text2), role });
    };
    const claim = { verdict: "applicable", binding };
    await write("resource-result.json", { providerId, runId, finalPassId, ...claim }, "resource-result");
    await write("resource-proof.json", { version: "installed-data-proof/1", observation: this.observations.get(providerId), origin }, "resource-proof");
    const manifest = {
      spec: "delivery-evidence/1",
      provider: { id: providerId, runId, finalPassId },
      candidate: { vcs: "git", treeSha: c.treeSha, headSha: c.headSha, deliverable: c.deliverable, base: c.base, workspaceId: c.workspaceId },
      runHistory: [{ preparedTreeSha: c.treeSha, evaluatedInPassId: finalPassId }],
      artifacts,
      attestation: { level: "self", signatures: [] },
      recordedAt: (/* @__PURE__ */ new Date()).toISOString(),
      claims: obligationIds.map((obligation) => ({ obligation, payloadSpec: "installed-data.applicable/1", payload: claim }))
    };
    const manifestPath = path10.join(allocation.runRoot.path, "manifest.json");
    await this.context.artifacts.writeTextFile(manifestPath, JSON.stringify(manifest));
    const wiring = await this.context.wire();
    const submitted = await this.publishing(async () => submitManifest2({ rootDir: this.context.rootDir, config: this.context.config, manifestPath }, {
      captureCandidate: wiring.captureCandidate,
      artifacts: this.context.artifacts,
      ...wiring.storageOptions,
      scopedPlan: await this.plan(),
      readOutput: this.readOutput,
      resourceBindings: await this.resourceBindings()
    }));
    if (submitted.status !== "accepted") throw new CheckSnapshotError("check_evidence_rejected", submitted.blockers.map((b) => b.code).join(", "));
    this.context.write(`applicable ${providerId}: matching native source/resources; consumer command omitted; origin ${origin.attempt.attemptId}`);
  }
  resourceOrigin(row) {
    if (row.attempt.status !== "passed" || !row.executionHeadSha) return void 0;
    const a = row.attempt;
    return {
      providerId: a.providerId,
      runId: a.origin.runId,
      attemptId: a.attemptId,
      generation: a.generation,
      inputDigest: a.inputDigest,
      profileDigest: a.profileDigest,
      source: { headSha: row.executionHeadSha, treeSha: a.origin.candidate.treeSha },
      status: "passed",
      superseded: false
    };
  }
  async closeCapturePhase() {
    this.capturePhaseOpen = false;
    await this.captureTail;
  }
  acceptsApplicability(provider, obligationIds) {
    return provider.check?.installedData !== void 0 && obligationIds.length > 0 && obligationIds.every((id) => this.context.config.obligations.find((o) => o.id === id)?.acceptedPayloadSpecs.includes("installed-data.applicable/1"));
  }
  onWorker(lane, task) {
    let locked = this.executionLocks.get(lane.snapshots);
    if (!locked) {
      locked = oneAtATime();
      this.executionLocks.set(lane.snapshots, locked);
    }
    const running = locked(task);
    this.executionTails.set(lane.snapshots, running.catch(() => void 0));
    return running;
  }
  /** `cancel` is a fail-fast gate's: once it aborts, the check stops and is recorded `interrupted` with `check_cancelled`. */
  execute(provider, obligationIds, deferSubmission = false, lane = this.serial, cancel) {
    this.capturePhaseOpen = false;
    return this.onWorker(lane, async () => {
      if (!deferSubmission && this.acceptsApplicability(provider, obligationIds)) {
        await this.closeCapturePhase();
        let applicable = false;
        try {
          applicable = (await this.scheduleCapture(() => this.captureDeclaredInstalledData(provider.id, lane), true)).applicable;
        } catch (error) {
          if (this.context.signal?.aborted || this.cleanupStarted) throw error;
          lane.write(`resource fallback ${provider.id}: ${error instanceof Error ? error.message : "capture unavailable"}`);
        }
        if (applicable) {
          await this.publishInstalledDataApplicability(provider.id, obligationIds);
          return;
        }
      }
      await this.executeInLane(provider, obligationIds, deferSubmission, lane, cancel);
    });
  }
  async executeInLane(provider, obligationIds, deferSubmission, lane, cancel) {
    await this.closeCapturePhase();
    const commandSignal = cancel ? AbortSignal.any([cancel.signal, ...this.context.signal ? [this.context.signal] : []]) : this.context.signal;
    const cancelled = () => cancel?.signal.aborted === true && !this.context.signal?.aborted;
    const started = Date.now(), check = provider.check, profile = this.context.config.scopedExecution.profiles.find((p) => p.id === check.scope.profile);
    const attempt = this.owned.get(provider.id) ?? await this.allocate(provider.id);
    const phases2 = [];
    let settingUp = true;
    this.phaseWriters.set(lane.snapshots, (phase, durationMs) => {
      if (settingUp) phases2.push({ phase: phase === "verification" ? "pre-command-verification" : phase, durationMs: Math.round(durationMs) });
      lane.write(`snapshot ${provider.id}: attempt ${attempt.attemptId}; ${phase} ${Math.round(durationMs)}ms`);
    });
    let payload = { outputs: [] }, terminal = false;
    let diagnostic = { availability: "available", phase: "snapshot-setup", failure: { unavailable: "not-failed" }, command: { unavailable: "not-started" } };
    let executionErrorCode;
    const store = this.stores.get(provider.id);
    const secrets = check.scope.environment.filter((e) => e.kind === "credential").map((e) => this.context.env[e.name]).filter((v) => !!v);
    try {
      const verificationOf = (setup2) => setup2.snapshot === "built" || check.physicalSource ? "inventory" : "index";
      const { snapshot, sharedOutputs, setup } = await this.acquireSnapshot(
        profile,
        lane,
        (result2, durationMs) => {
          diagnostic = { ...diagnostic, dependency: scopedDependencyDiagnostic(result2, durationMs, secrets) };
        },
        check.physicalSource,
        (setup2) => {
          diagnostic = { ...diagnostic, setup: { ...setup2, verification: verificationOf(setup2), phases: phases2 } };
        }
      );
      const verification = setup.snapshot === "built" ? "inventory" : snapshot.reusedVerification;
      diagnostic = { ...diagnostic, phase: "pre-command-verification", setup: { ...setup, verification, phases: phases2 } };
      const removed = [...check.outputs ?? [], ...sharedOutputs.filter((o) => !profile.mutableOutputs.includes(o))];
      const remove = async () => {
        for (const output of removed) await rm3(path10.join(snapshot.rootDir, output), { recursive: true, force: true });
      };
      if (verification === "index") {
        await snapshot.verify({ outputs: profile.mutableOutputs, unchanged: { allowed: [...profile.mutableOutputs, ...this.ranOutputs.get(snapshot) ?? []] } });
        await remove();
      } else {
        await remove();
        await snapshot.verify({ outputs: profile.mutableOutputs });
      }
      settingUp = false;
      lane.write(`setup ${provider.id}: attempt ${attempt.attemptId}; ${setup.action} on a ${setup.snapshot} tree (clone ${setup.clone}), ${verification} verification; ${Date.now() - started}ms`);
      if (cancelled()) throw new CheckSnapshotError("check_cancelled", `Declared scoped check ${provider.id} was cancelled before its command, after ${cancel.after()} failed.`);
      const injected = Object.fromEntries(check.scope.environment.filter((e) => this.context.env[e.name] !== void 0).map((e) => [e.name, this.context.env[e.name]]));
      lane.write(`checking ${provider.id}: attempt ${attempt.attemptId}`);
      diagnostic = { ...diagnostic, phase: "command" };
      const commandHome = path10.join(snapshot.commandRoot, attempt.attemptId, "home"), commandTemp = path10.join(snapshot.commandRoot, attempt.attemptId, "tmp");
      await mkdir4(commandHome, { recursive: true });
      await mkdir4(commandTemp, { recursive: true });
      diagnostic = { ...diagnostic, command: { unavailable: "not-completed" } };
      const result = await createExecPort3().run({ command: check.command[0], args: check.command.slice(1), cwd: path10.join(snapshot.rootDir, check.scope.cwd), env: { ...snapshot.environment, ...injected, HOME: commandHome, TMPDIR: commandTemp }, timeoutMs: check.timeoutMs, maxBuffer: 1024 * 1024, ...commandSignal ? { signal: commandSignal } : {} });
      executionErrorCode = result.errorCode;
      diagnostic = { ...diagnostic, command: scopedCommandDiagnostic(result, secrets) };
      payload = { outputs: [], durationMs: Date.now() - started, log: redactScopedStreams(result, secrets).slice(-4e3), dependencyDigest: snapshot.dependencyDigest };
      if (cancelled() && result.errorCode === "ABORT_ERR") throw new CheckSnapshotError("check_cancelled", `Declared scoped check ${provider.id} was cancelled, its process group signalled, after ${cancel.after()} failed.`);
      if (result.code !== 0 || this.context.signal?.aborted) throw new CheckSnapshotError("check_command_failed", `Declared scoped check ${provider.id} did not complete successfully (exit ${result.code}).`);
      diagnostic = { ...diagnostic, phase: "post-command-verification" };
      await snapshot.verify({ outputs: profile.mutableOutputs });
      const ran = this.ranOutputs.get(snapshot) ?? /* @__PURE__ */ new Set();
      for (const output of profile.mutableOutputs) ran.add(output);
      this.ranOutputs.set(snapshot, ran);
      diagnostic = { ...diagnostic, phase: "output-capture" };
      const outputs = await captureCheckOutputSnapshots(snapshot.rootDir, check.outputs ?? []);
      if (!outputs || outputs.some((o) => secrets.some((secret) => Buffer.from(o.base64, "base64").includes(Buffer.from(secret))))) throw new CheckSnapshotError("check_output_missing", "A retained output is absent, corrupt, escaped or contains credential bytes.");
      payload = { ...payload, outputs, diagnostic: { ...diagnostic, phase: "complete" } };
      const installed = this.installed.get(provider.id);
      if (installed?.snapshot === snapshot) payload = { ...payload, resourceScope: structuredClone(check.scope), resourceApplicability: createResourceApplicabilityProjection({
        origin: this.resourceOrigin({ attempt: { ...attempt, status: "passed" }, executionHeadSha: this.candidate.headSha }),
        consumerId: installed.binding.declaration.consumerId,
        declarationDigest: installed.binding.declarationDigest,
        sourceApplicabilityDigest: installed.binding.sourceApplicabilityDigest,
        capture: installed.capture
      }) };
      await store.finish(attempt, "passed", payload);
      terminal = true;
      const scopedPlan = await this.plan();
      const wiring = await this.context.wire();
      const binding = (await captureCheckBindings(this.context.rootDir, this.context.config, this.candidate, { ...wiring.storageOptions, scopedPlan, readOutput: this.readOutput }))[provider.id];
      if (!binding || binding.scopedAttemptDigest !== digestCanonical5({ ...attempt, status: "passed" })) throw new CheckSnapshotError("check_attempt_superseded", "A newer attempt superseded this completion before evidence publication.");
      const runId = attempt.attemptId, finalPassId = "pass-1";
      const allocation = await this.context.artifacts.allocateRunRoot({ providerId: provider.id, runId });
      if (!allocation.ok) throw new CheckSnapshotError("check_artifact_unavailable", "Cannot allocate scoped check evidence.");
      const artifacts = [];
      const write = async (name, value2, role) => {
        const text2 = JSON.stringify(value2);
        await this.context.artifacts.writeTextFile(path10.join(allocation.runRoot.path, name), text2);
        artifacts.push({ path: name, sha256: sha256Hex3(text2), role });
      };
      for (const [index, output] of outputs.entries()) await write(`check-output-${index}.json`, { path: output.path, base64: output.base64 }, "check-output");
      const claim = { verdict: "green", exitCode: 0, binding };
      await write("check-result.json", { providerId: provider.id, runId, finalPassId, ...claim }, "check-result");
      await write("scoped-inputs.json", { observation: this.observations.get(provider.id), attempt: { ...attempt, status: "passed" }, durationMs: payload.durationMs, dependencyDigest: payload.dependencyDigest, ...payload.resourceApplicability ? { resourceApplicability: payload.resourceApplicability } : {} }, "scoped-inputs");
      const c = this.candidate;
      const manifest = { spec: "delivery-evidence/1", provider: { id: provider.id, runId, finalPassId }, candidate: { vcs: "git", treeSha: c.treeSha, headSha: c.headSha, deliverable: c.deliverable, base: c.base, workspaceId: c.workspaceId }, runHistory: [{ preparedTreeSha: c.treeSha, evaluatedInPassId: finalPassId }], artifacts, attestation: { level: "self", signatures: [] }, recordedAt: (/* @__PURE__ */ new Date()).toISOString(), claims: obligationIds.map((obligation) => ({ obligation, payloadSpec: "checks.passed/1", payload: claim })) };
      const manifestPath = path10.join(allocation.runRoot.path, "manifest.json");
      await this.context.artifacts.writeTextFile(manifestPath, JSON.stringify(manifest));
      const submit = () => this.publishing(async () => {
        const outcome2 = await submitManifest2({ rootDir: this.context.rootDir, config: this.context.config, manifestPath }, { captureCandidate: wiring.captureCandidate, artifacts: this.context.artifacts, ...wiring.storageOptions, scopedPlan: await this.plan(), readOutput: this.readOutput, resourceBindings: await this.resourceBindings() });
        if (outcome2.status !== "accepted") throw new CheckSnapshotError("check_evidence_rejected", outcome2.blockers.map((b) => b.code).join(", "));
      });
      if (deferSubmission) this.submissions.push(submit);
      else await submit();
      lane.write(`passed ${provider.id}: ${Date.now() - started}ms including snapshot setup; retained ${runId}`);
    } catch (error) {
      const status = this.context.signal?.aborted || error instanceof CheckSnapshotError && error.code === "check_cancelled" ? "interrupted" : "failed";
      if (!terminal) await store.finish(attempt, status, {
        ...payload,
        durationMs: Date.now() - started,
        diagnostic: { ...diagnostic, failure: scopedDiagnosticFailure(error, executionErrorCode) },
        ...error instanceof CheckSnapshotError ? { log: `${payload.log ?? ""}
${error.code}`.slice(-4e3) } : {}
      });
      const ran = diagnostic.phase === "snapshot-setup" && diagnostic.dependency ? { argv: profile.dependencies.command, cwd: ".", result: diagnostic.dependency.command } : { argv: check.command, cwd: check.scope.cwd, result: diagnostic.command };
      lane.write([
        `${status} ${provider.id}: ${error instanceof CheckSnapshotError ? error.code : "unclassified"}; attempt ${attempt.attemptId} retained at ${store.terminalPath(attempt)}`,
        `  command (cwd ${ran.cwd}): ${ran.argv.map(shownArgument).join(" ")}`,
        ..."unavailable" in ran.result ? [`  output ${ran.result.unavailable}`] : [`  exit ${ran.result.exitCode ?? "unavailable"}; output tail (last 40 lines):`, ...ran.result.outputTail.trimEnd().split("\n").slice(-40).map((line) => `    ${line}`)]
      ].join("\n"));
      const damaged = lane.snapshots.get(setupKey(profile, check.physicalSource));
      lane.snapshots.delete(setupKey(profile, check.physicalSource));
      await damaged?.cleanup();
      throw error;
    }
  }
  async cleanup() {
    this.cleanupStarted = true;
    await this.closeCapturePhase();
    await Promise.all(this.executionTails.values());
    this.prewarming.abort();
    await Promise.all(this.timingWrites);
    for (const snapshots of this.workers) {
      for (const snapshot of snapshots.values()) await snapshot.cleanup();
      snapshots.clear();
    }
    const bases = [...this.bases.values()];
    this.bases.clear();
    for (const base of bases) await (await base.catch(() => void 0))?.cleanup();
  }
};

// packages/cli/src/declared-checks.ts
import { randomUUID as randomUUID7 } from "node:crypto";
import path11 from "node:path";
import { captureCheckBindings as captureCheckBindings2, captureCheckOutputSnapshots as captureCheckOutputSnapshots2, digestCanonical as digestCanonical6, classifyCandidateDrift, computeCheckWiringFingerprint, createBlocker as createBlocker3, createExecPort as createExecPort4, sha256Hex as sha256Hex4, submitManifest as submitManifest3 } from "./kernel.mjs";
async function runDeclaredCheck(context, provider, obligationIds, before, scoped = {}) {
  const check = provider.check;
  const wiring = await context.wire();
  const fail = (code, summary, details) => [createBlocker3({
    code,
    source: { kind: "command", id: "delivery-harness.cli.gate" },
    summary,
    ...details === void 0 ? {} : { details },
    remediations: [{ id: "repair-declared-check", kind: "manual_action", summary: "Fix the declared check or its inputs, prepare the stable candidate and run gate again." }]
  })];
  const start = await wiring.captureCandidate();
  if (!start.ok) return start.blockers;
  if (classifyCandidateDrift(before, start.candidate).length) return fail("check_candidate_changed", "The candidate changed after review admission and before the check started.");
  const fingerprint = await computeCheckWiringFingerprint(context.rootDir, context.config, wiring.storageOptions);
  context.write(`checking ${provider.id}`);
  const result = await createExecPort4().run({
    command: check.command[0],
    args: check.command.slice(1),
    cwd: context.rootDir,
    env: Object.fromEntries(Object.entries(context.env).filter((entry) => entry[1] !== void 0)),
    timeoutMs: check.timeoutMs,
    maxBuffer: 1024 * 1024,
    ...context.signal === void 0 ? {} : { signal: context.signal }
  });
  if (result.code !== 0 || context.signal?.aborted) return fail("check_command_failed", `Declared check ${provider.id} did not complete successfully.`, `exit ${result.code}${result.errorCode === void 0 ? "" : ` (${result.errorCode})`}
${`${result.stdout}
${result.stderr}`.slice(-4e3)}`);
  const captured = await wiring.captureCandidate();
  if (!captured.ok) return captured.blockers;
  if (classifyCandidateDrift(before, captured.candidate).length || start.candidate.headSha !== captured.candidate.headSha || fingerprint !== await computeCheckWiringFingerprint(context.rootDir, context.config, wiring.storageOptions)) return fail("check_candidate_changed", "The candidate, base or wiring changed while the check ran.");
  const binding = (await captureCheckBindings2(context.rootDir, context.config, captured.candidate, { ...wiring.storageOptions, ...scoped }))[provider.id];
  if (binding === void 0) return fail("check_output_missing", `Declared check ${provider.id} has a missing, unreadable, oversized or escaped output.`);
  const runId = randomUUID7(), finalPassId = "pass-1";
  const allocation = await context.artifacts.allocateRunRoot({ providerId: provider.id, runId });
  if (!allocation.ok) return fail("check_artifact_unavailable", "Cannot allocate the declared check evidence root.");
  const snapshots = await captureCheckOutputSnapshots2(context.rootDir, check.outputs ?? []);
  if (snapshots === void 0 || digestCanonical6(snapshots.map(({ path: path23, sha256 }) => ({ path: path23, sha256 }))) !== binding.outputsDigest) return fail("check_output_missing", "Declared outputs changed before evidence retention.");
  const outputArtifacts = [];
  for (const [index, output] of snapshots.entries()) {
    const contents = JSON.stringify({ path: output.path, base64: output.base64 });
    const artifactPath = `check-output-${index}.json`;
    await context.artifacts.writeTextFile(path11.join(allocation.runRoot.path, artifactPath), contents);
    outputArtifacts.push({ path: artifactPath, sha256: sha256Hex4(contents), role: "check-output" });
  }
  const payload = { verdict: "green", exitCode: 0, binding };
  const terminal = JSON.stringify({ providerId: provider.id, runId, finalPassId, ...payload });
  await context.artifacts.writeTextFile(path11.join(allocation.runRoot.path, "check-result.json"), terminal);
  const candidate = captured.candidate;
  const manifest = {
    spec: "delivery-evidence/1",
    provider: { id: provider.id, runId, finalPassId },
    candidate: { vcs: "git", treeSha: candidate.treeSha, headSha: candidate.headSha, deliverable: candidate.deliverable, base: candidate.base, workspaceId: candidate.workspaceId },
    runHistory: [{ preparedTreeSha: candidate.treeSha, evaluatedInPassId: finalPassId }],
    artifacts: [{ path: "check-result.json", sha256: sha256Hex4(terminal), role: "check-result" }, ...outputArtifacts],
    attestation: { level: "self", signatures: [] },
    recordedAt: (/* @__PURE__ */ new Date()).toISOString(),
    claims: obligationIds.map((obligation) => ({ obligation, payloadSpec: "checks.passed/1", payload }))
  };
  const manifestPath = path11.join(allocation.runRoot.path, "manifest.json");
  await context.artifacts.writeTextFile(manifestPath, JSON.stringify(manifest));
  const outcome2 = await submitManifest3({ rootDir: context.rootDir, config: context.config, manifestPath }, { captureCandidate: wiring.captureCandidate, artifacts: context.artifacts, ...wiring.storageOptions, ...scoped });
  return outcome2.status === "accepted" ? [] : outcome2.blockers;
}

// packages/cli/src/scoped-budget.ts
import { scopedBudgetOffenders } from "./kernel.mjs";
function refuseOverBudget(config) {
  const { overCap, overBudget, totalMs } = scopedBudgetOffenders(config);
  if (overCap.length === 0 && overBudget.length === 0) return;
  const { budgetMs, checkBudgetMs } = config.scopedExecution;
  const timeout = (id) => config.providers.find((p) => p.id === id).check.timeoutMs;
  const reasons = [
    ...overCap.map((id) => `${id} declares timeoutMs ${timeout(id)} over checkBudgetMs ${checkBudgetMs}`),
    ...overBudget.length ? [`${overBudget.join(", ")} ${overBudget.length === 1 ? "pushes" : "push"} the declared total to ${totalMs}ms over budgetMs ${budgetMs}`] : []
  ];
  throw new CheckSnapshotError("scoped_budget_exceeded", `The scoped check selection exceeds its declared budget, so no check ran: ${reasons.join("; ")}.`);
}

// packages/cli/src/commands/gate.ts
import { computeDeliverableIdentity as computeDeliverableIdentity2, digestCanonical as digestCanonical7, runAdmission } from "./kernel.mjs";

// packages/cli/src/delivery-posture.ts
import { deliveryPostureRefusedOutsideRoot, resolveDeliveryPosture } from "./kernel.mjs";
async function focusedOutcome(context) {
  const resolved = await resolveDeliveryPosture({
    rootDir: context.rootDir,
    baseRef: context.config.baseRef,
    ...context.signal === void 0 ? {} : { signal: context.signal }
  });
  if (context.signal?.aborted) throw new CliInterruption();
  if (resolved.posture === "focused") {
    return {
      kind: "ok",
      unobserved: true,
      summary: [
        "outcome: focused",
        `posture: ${resolved.posture}`,
        `reason: ${oneLine(resolved.reason, 512)}`,
        `source: ${oneLine(resolved.source, 256)}`,
        `base tip: ${resolved.baseTipSha ?? "(none)"}`
      ].join("\n")
    };
  }
  if (resolved.code === "posture_malformed" || deliveryPostureRefusedOutsideRoot(resolved)) context.writeError(`${resolved.code}: ${oneLine(resolved.reason, 512)}`);
  return void 0;
}

// packages/cli/src/commands/gate.ts
var scopedCheckBlocker = (error) => commandBlocker({ code: error.code, sourceId: "delivery-harness.cli.gate", summary: error.message, remediations: [{ id: "repair-scoped-check", kind: "manual_action", summary: "Repair the declared scoped check or its execution profile and run the gate again." }] });
async function providerAdmission(context, options, session) {
  const admit = async (input2, options2) => runAdmission(input2, { ...options2, ...session ? { scopedPlan: await session.plan(), readOutput: session.readOutput, resourceBindings: await session.resourceBindings() } : {} });
  if (session) {
    await session.fenceNonReusable(context.config.providers.filter((p) => p.check?.scope).map((p) => p.id));
  }
  const wiring = await context.wire();
  const admissionOptions = {
    captureCandidate: wiring.captureCandidate,
    projectActivation: wiring.projectActivation,
    ...wiring.storageOptions
  };
  const input = {
    rootDir: context.rootDir,
    config: context.config,
    context: context.classifyContext(),
    ...options.includeInjectedLiveResults && context.liveResults !== void 0 ? { liveResults: context.liveResults } : {}
  };
  const finalAdmissionOptions = {
    ...admissionOptions,
    ...options.allowPrompt && context.promptForWaiver !== void 0 ? { promptForWaiver: context.promptForWaiver } : {}
  };
  if (!context.config.providers.some((provider) => provider.command !== void 0 || provider.check !== void 0)) {
    return admit(input, finalAdmissionOptions);
  }
  const liveResults = options.includeInjectedLiveResults ? [...context.liveResults ?? []] : [];
  const invokeProvider = context.invokeProvider;
  const besideOutcomes = /* @__PURE__ */ new Map();
  const attempted = /* @__PURE__ */ new Set();
  const attemptBlockers = [];
  let admission = await admit(input, admissionOptions);
  await session?.explainReuse((admission.decision?.resolutions ?? []).flatMap((r) => r.kind === "satisfied_evidence" ? [r.providerId] : []));
  const failFast = context.config.scopedExecution?.failFast === true;
  let failedFast = false;
  while (!failedFast && !admission.admitted && admission.decision !== void 0 && admission.candidate !== void 0) {
    const requested = /* @__PURE__ */ new Map();
    for (const resolution of admission.decision.resolutions) {
      if (resolution.kind !== "blocked") continue;
      const obligation = context.config.obligations.find((entry) => entry.id === resolution.obligationId);
      if (obligation === void 0) continue;
      const missingCode = obligation.freshness === "live" ? "live_provider_missing" : "review_evidence_missing";
      for (const finding of resolution.providerFindings ?? []) {
        if (finding.code !== missingCode || finding.providerId === void 0 || attempted.has(finding.providerId)) continue;
        const registration2 = context.config.providers.find((provider) => provider.id === finding.providerId);
        if (registration2 === void 0 || registration2.command === void 0 && registration2.check === void 0 || !obligation.providers.includes(registration2.id)) continue;
        if (registration2.check !== void 0 && (obligation.freshness !== "exact_candidate" || !obligation.acceptedPayloadSpecs.includes("checks.passed/1") || admission.decision.resolutions.some((resolution2) => resolution2.kind !== "satisfied_evidence" && resolution2.kind !== "not_applicable" && resolution2.kind !== "waived" && context.config.obligations.find((entry2) => entry2.id === resolution2.obligationId)?.acceptedPayloadSpecs.some((spec) => spec === "review.green/1" || spec === "review.green/2")))) continue;
        const entry = requested.get(registration2.id) ?? { obligationIds: [], requiresEvidence: false, needsLiveResult: false };
        entry.obligationIds.push(obligation.id);
        entry.requiresEvidence ||= obligation.freshness === "exact_candidate";
        entry.needsLiveResult ||= obligation.freshness === "live";
        requested.set(registration2.id, entry);
      }
    }
    const scoped = session !== void 0 && session.concurrency > 1 ? [...requested].filter(([id]) => context.config.providers.find((p) => p.id === id).check?.scope) : [];
    if (session !== void 0 && scoped.length > 0) {
      for (const [id] of scoped) attempted.add(id);
      const shared = (id) => context.config.obligations.some((o) => (o.providerPolicy ?? "all") === "existential" && o.providers.includes(id) && o.providers.length > 1);
      const beside = invokeProvider === void 0 ? [] : [...requested].filter(([id, request2]) => {
        const registration2 = context.config.providers.find((p) => p.id === id);
        return registration2.independentOfScopedChecks === true && registration2.command !== void 0 && request2.needsLiveResult && !request2.requiresEvidence && !shared(id) && !besideOutcomes.has(id);
      });
      const cancelBeside = new AbortController();
      let cancelledAfter;
      const besideRuns = [];
      const startBeside = () => {
        if (besideRuns.length > 0 || cancelBeside.signal.aborted) return;
        for (const [providerId2, request2] of beside) besideRuns.push(invokeProvider({
          providerId: providerId2,
          requiresEvidence: false,
          signal: cancelBeside.signal,
          payload: { gateId: context.config.gateId, providerId: providerId2, obligationIds: [...new Set(request2.obligationIds)].sort(), candidate: admission.candidate }
        }).then((result2) => ({ result: result2 }), (error) => ({ error })));
      };
      let failures;
      try {
        failures = await session.executeAll(
          scoped.map(([id, request2]) => ({ provider: context.config.providers.find((p) => p.id === id), obligationIds: request2.obligationIds })),
          (failed) => {
            cancelledAfter = failed;
            cancelBeside.abort();
          },
          startBeside
        );
      } catch (error) {
        cancelBeside.abort();
        await Promise.all(besideRuns);
        throw error;
      }
      const settled = await Promise.all(besideRuns);
      if (context.signal?.aborted) throw new CliInterruption();
      attemptBlockers.push(...failures.map(scopedCheckBlocker));
      failedFast = failFast && failures.length > 0;
      for (const [index, [providerId2]] of beside.entries()) {
        if (cancelledAfter !== void 0) context.write(`interrupted ${providerId2}: check_cancelled after ${cancelledAfter} failed`);
        else besideOutcomes.set(providerId2, { ...settled[index], obligationIds: [...new Set(beside[index][1].obligationIds)].sort() });
      }
      admission = await admit({ ...input, ...liveResults.length === 0 ? {} : { liveResults } }, admissionOptions);
      continue;
    }
    const next = requested.entries().next().value;
    if (next === void 0) break;
    const [providerId, request] = next;
    attempted.add(providerId);
    const registration = context.config.providers.find((provider) => provider.id === providerId);
    if (registration.check !== void 0) {
      if (registration.check.scope && session) {
        try {
          await session.execute(registration, request.obligationIds);
        } catch (error) {
          if (!(error instanceof CheckSnapshotError)) throw error;
          attemptBlockers.push(scopedCheckBlocker(error));
          if (failFast) {
            failedFast = true;
            await session.cancelUnstarted([...requested.keys()].filter((id) => !attempted.has(id) && context.config.providers.find((p) => p.id === id).check?.scope), providerId);
          }
        }
      } else attemptBlockers.push(...await runDeclaredCheck(context, registration, request.obligationIds, admission.candidate, session ? { scopedPlan: await session.plan(), readOutput: session.readOutput, resourceBindings: await session.resourceBindings() } : {}));
      admission = await admit({ ...input, ...liveResults.length === 0 ? {} : { liveResults } }, admissionOptions);
      continue;
    }
    const stored = besideOutcomes.get(providerId);
    const early = stored && digestCanonical7(stored.obligationIds) === digestCanonical7([...new Set(request.obligationIds)].sort()) ? stored : void 0;
    if (early && "error" in early) throw early.error;
    const result = early ? early.result : await context.invokeProvider?.({
      providerId,
      requiresEvidence: request.requiresEvidence,
      payload: {
        gateId: context.config.gateId,
        providerId,
        obligationIds: [...new Set(request.obligationIds)].sort(),
        candidate: admission.candidate
      }
    });
    if (result === void 0) continue;
    if (result.kind === "interrupted") throw new CliInterruption("Provider invocation interrupted before a trustworthy terminal outcome.");
    if (result.kind === "blocked") {
      attemptBlockers.push(...result.blockers);
      if (request.needsLiveResult) {
        liveResults.push({ providerId, runId: result.runId, status: "failed", findings: [] });
      }
    } else if (request.needsLiveResult) {
      liveResults.push(result.liveResult);
    }
    admission = await admit(
      { ...input, ...liveResults.length === 0 ? {} : { liveResults } },
      admissionOptions
    );
  }
  if (admission.admitted) return { ...admission, observedLiveResults: liveResults };
  const final = await admit(
    { ...input, ...liveResults.length === 0 ? {} : { liveResults } },
    finalAdmissionOptions
  );
  return attemptBlockers.length === 0 || final.admitted ? { ...final, observedLiveResults: liveResults } : { ...final, observedLiveResults: liveResults, blockers: [...attemptBlockers, ...final.blockers] };
}
async function runProviderBackedAdmission(context, options) {
  refuseOverBudget(context.config);
  let session;
  try {
    if (context.config.providers.some((p) => p.check?.scope)) {
      const capture2 = await (await context.wire()).captureCandidate();
      if (capture2.ok) session = await ScopedChecks.create(context, capture2.candidate);
    }
    return await providerAdmission(context, options, session);
  } finally {
    await session?.cleanup();
  }
}
async function runGate(context) {
  const result = await runProviderBackedAdmission(context, { allowPrompt: true, includeInjectedLiveResults: true });
  if (result.admitted) {
    const waiverNote = result.waiver === "accepted" ? ` (waived: ${result.waivedObligationIds.join(", ")})` : "";
    const kinds = (result.decision?.resolutions ?? []).map((resolution) => `${resolution.obligationId}=${resolution.kind}`);
    let digest;
    if (result.candidate !== void 0) {
      try {
        digest = await computeDeliverableIdentity2({
          rootDir: context.rootDir,
          treeSha: result.candidate.treeSha,
          config: { ...context.config, computingIdentityVersion: "validation-tree/v1", reviewNeutral: context.config.recordNeutral }
        });
      } catch {
      }
    }
    return { kind: "ok", summary: `admitted${waiverNote}: ${kinds.join(", ")}`, ...digest === void 0 ? {} : { digest } };
  }
  return { kind: "blocked", blockers: [...result.blockers] };
}
var gateCommand = {
  capturesCandidate: true,
  name: "gate",
  sourceId: "delivery-harness.cli.gate",
  summary: "Evaluate the delivery gate for the current candidate.",
  usage: "Usage: delivery-harness gate\nTakes no arguments; a waiver is offered only under a real TTY.",
  async run(context) {
    const unexpected = context.args[0];
    if (unexpected !== void 0) {
      return { kind: "usage", message: `gate takes no arguments, and ${oneLine(unexpected, 64)} is one.
${gateCommand.usage}` };
    }
    const focused = await focusedOutcome(context);
    if (focused !== void 0) return focused;
    return runGate(context);
  }
};

// packages/cli/src/commands/prepare.ts
import {
  classifyCandidateDrift as classifyCandidateDrift2,
  computePreparationFingerprint,
  computeDeliverableIdentity as computeDeliverableIdentity3,
  evaluatePreparationReceipt as evaluatePreparationReceipt2,
  createBlocker as createBlocker4,
  createExecPort as createExecPort5,
  invalidatePreparationReceipt,
  revokePreparationAttempt,
  publishPreparationReceipt
} from "./kernel.mjs";
var USAGE2 = "Usage: delivery-harness prepare [--refresh-record-neutral]";
var prepareCommand = {
  capturesCandidate: true,
  name: "prepare",
  sourceId: "delivery-harness.cli.prepare",
  summary: "Run preparation checks; --refresh-record-neutral permits proven artifact-only receipt refresh.",
  // The text prepare has always answered `--help` with, unchanged; the
  // boundary is what prints it now, for every command rather than this one.
  usage: `${USAGE2}
Ordinary prepare always runs mechanical checks. The refresh flag reuses prior success only when strict validation, policy, wiring and base are unchanged; otherwise it runs the checks.
Scoped mechanical checks run the first declared check first and the rest cheapest first by recorded duration, one dependency setup at a time, stopping at the first failure.`,
  async run(context) {
    if (context.args.length > 1 || context.args.length === 1 && context.args[0] !== "--refresh-record-neutral") {
      return { kind: "usage", message: USAGE2 };
    }
    if (context.config.providers.some((p) => p.check?.scope) && !context.config.scopedExecution) throw new CheckSnapshotError("scoped_executor_required", "Scoped execution profiles are required before preparation commands may run.");
    refuseOverBudget(context.config);
    let scoped;
    const refreshRecordNeutral = context.args[0] === "--refresh-record-neutral";
    const wiring = await context.wire();
    let retainedAttemptId;
    let ownedAttemptId;
    let refreshed = false;
    try {
      let capture2;
      let reusableFingerprint;
      let attemptId;
      try {
        capture2 = await wiring.captureCandidate();
        if (capture2.ok && refreshRecordNeutral) {
          const previous = await evaluatePreparationReceipt2(
            context.rootDir,
            { config: context.config, candidate: capture2.candidate },
            { ...wiring.storageOptions, allowValidationEquivalent: true }
          );
          if (previous.prepared && previous.receipt.attemptId !== void 0) {
            reusableFingerprint = previous.receipt.preparationFingerprint;
            retainedAttemptId = previous.receipt.attemptId;
          }
        }
        if (retainedAttemptId === void 0 && context.config.scopedExecution?.repairCommands?.length) {
          ownedAttemptId = await invalidatePreparationReceipt(context.rootDir, context.config, wiring.storageOptions);
          const beforeRepair = await computePreparationFingerprint(context.rootDir, context.config, wiring.storageOptions);
          for (const repair of context.config.scopedExecution.repairCommands) {
            context.write(`repairing ${repair.id}`);
            const result = await createExecPort5().run({
              command: repair.command[0],
              args: repair.command.slice(1),
              cwd: context.rootDir,
              env: Object.fromEntries(Object.entries(context.env).filter((e) => e[1] !== void 0)),
              timeoutMs: repair.timeoutMs,
              maxBuffer: 1024 * 1024,
              ...context.signal ? { signal: context.signal } : {}
            });
            if (context.signal?.aborted) throw new CliInterruption();
            if (result.code !== 0) throw new CheckSnapshotError("preparation_repair_failed", `Source repair ${repair.id} failed; fix it and prepare again.`);
          }
          if (beforeRepair !== await computePreparationFingerprint(context.rootDir, context.config, wiring.storageOptions)) throw new CheckSnapshotError("preparation_candidate_changed", "Repair changed preparation wiring; reload the configuration and prepare again.");
          capture2 = await wiring.captureCandidate();
        }
      } finally {
        attemptId = retainedAttemptId ?? ownedAttemptId ?? await invalidatePreparationReceipt(context.rootDir, context.config, wiring.storageOptions);
        ownedAttemptId = attemptId;
      }
      if (!capture2.ok) {
        return { kind: "blocked", blockers: [...capture2.blockers] };
      }
      const fingerprint = await computePreparationFingerprint(context.rootDir, context.config, wiring.storageOptions);
      const reusable = reusableFingerprint === fingerprint;
      const preparation = reusable ? { checks: "reused", reason: "validation-equivalent" } : { checks: "executed", reason: !refreshRecordNeutral ? "ordinary" : retainedAttemptId === void 0 ? "receipt-not-reusable" : "preparation-fingerprint-changed" };
      if (!reusable && retainedAttemptId !== void 0) {
        attemptId = await invalidatePreparationReceipt(context.rootDir, context.config, wiring.storageOptions);
        ownedAttemptId = attemptId;
        retainedAttemptId = void 0;
        if (context.config.scopedExecution?.repairCommands?.length) throw new CheckSnapshotError("preparation_candidate_changed", "Preparation wiring changed after repairs were skipped; reload the configuration and prepare again.");
      }
      let validationDigest = await computeDeliverableIdentity3({
        rootDir: context.rootDir,
        treeSha: capture2.candidate.treeSha,
        config: { ...context.config, computingIdentityVersion: "validation-tree/v1", reviewNeutral: context.config.recordNeutral }
      });
      if (reusable) context.write("reusing preparation checks: strict validation projection, base, policy and wiring unchanged");
      const exec2 = createExecPort5();
      const env = Object.fromEntries(Object.entries(context.env).filter((entry) => entry[1] !== void 0));
      for (const check of reusable ? [] : context.config.preparationCommands ?? []) {
        context.write(`preparing ${check.id}`);
        const result = await exec2.run({
          command: check.command[0],
          args: check.command.slice(1),
          cwd: context.rootDir,
          env,
          timeoutMs: check.timeoutMs,
          maxBuffer: 1024 * 1024,
          ...context.signal === void 0 ? {} : { signal: context.signal }
        });
        if (result.code !== 0 || context.signal?.aborted) {
          return {
            kind: "blocked",
            blockers: [createBlocker4({
              code: "preparation_command_failed",
              source: { kind: "command", id: "delivery-harness.cli.prepare" },
              summary: `Preparation check ${check.id} failed; no receipt was published.`,
              details: `exit ${result.code}${result.errorCode === void 0 ? "" : ` (${result.errorCode})`}
${`${result.stdout}
${result.stderr}`.slice(-4e3)}`,
              remediations: [{ id: "repair-preparation-check", kind: "manual_action", summary: `Fix ${check.id}, then run prepare again.` }]
            })]
          };
        }
      }
      if (context.signal?.aborted) throw new CliInterruption();
      const finalCapture = await wiring.captureCandidate();
      if (!finalCapture.ok) return { kind: "blocked", blockers: [...finalCapture.blockers] };
      const after = finalCapture.candidate;
      if (classifyCandidateDrift2(capture2.candidate, after).length > 0 || capture2.candidate.headSha !== after.headSha || capture2.candidate.mode !== after.mode || fingerprint !== await computePreparationFingerprint(context.rootDir, context.config, wiring.storageOptions)) {
        return {
          kind: "blocked",
          blockers: [createBlocker4({
            code: "preparation_candidate_changed",
            source: { kind: "command", id: "delivery-harness.cli.prepare" },
            summary: "The candidate, base, or preparation wiring changed while checks ran; no receipt was published.",
            remediations: [{ id: "prepare-stable-candidate", kind: "manual_action", summary: "Commit any intended changes and prepare the stable candidate again." }]
          })]
        };
      }
      if (context.config.scopedExecution) {
        capture2 = finalCapture;
        validationDigest = await computeDeliverableIdentity3({ rootDir: context.rootDir, treeSha: capture2.candidate.treeSha, config: { ...context.config, computingIdentityVersion: "validation-tree/v1", reviewNeutral: context.config.recordNeutral } });
        if (context.config.scopedExecution.mechanicalProviders.length === 0) {
          await ScopedChecks.validatePreparation(context, capture2.candidate);
        } else {
          scoped = await ScopedChecks.create(context, capture2.candidate);
          await scoped?.satisfyMechanical();
        }
      }
      const published = await publishPreparationReceipt(
        context.rootDir,
        { config: context.config, candidate: capture2.candidate, attemptId, validationDigest },
        wiring.storageOptions
      );
      if (context.signal?.aborted) throw new CliInterruption();
      await scoped?.submitMechanical();
      await scoped?.cleanup();
      refreshed = true;
      return {
        kind: "ok",
        preparation,
        summary: [
          `prepared ${context.config.gateId}: tree ${capture2.candidate.treeSha} (${capture2.candidate.mode}); receipt ${published.path}`,
          `  treeSha ${capture2.candidate.treeSha}`
        ].join("\n")
      };
    } finally {
      try {
        await scoped?.cleanup();
      } finally {
        if (ownedAttemptId !== void 0 && !refreshed) await revokePreparationAttempt(context.rootDir, context.config, ownedAttemptId, wiring.storageOptions);
      }
    }
  }
};

// packages/cli/src/commands/record.ts
import {
  buildDeliveryRecord,
  candidateTreeEvidenceReader as candidateTreeEvidenceReader2,
  computePreparationFingerprint as computePreparationFingerprint2,
  capturePortableEvidenceContext as capturePortableEvidenceContext2,
  repositoryEvidenceReader as repositoryEvidenceReader2,
  capturePortableVerificationInputs,
  verifyDeliveryRecord,
  deliveryRecordBytes,
  deliveryRecordPathFor as deliveryRecordPathFor3,
  discoverRecords as discoverRecords2,
  readCompiledRepositoryPolicy
} from "./kernel.mjs";
import path13 from "node:path";

// packages/cli/src/record-retention.ts
import { createHash, randomUUID as randomUUID8 } from "node:crypto";
import { execFile as execFile2 } from "node:child_process";
import { constants as fsConstants } from "node:fs";
import { chmod as chmod2, lstat as lstat2, mkdir as mkdir5, open, readFile as readFile4, readdir as readdir3, rename as rename3, unlink as unlink2 } from "node:fs/promises";
import path12 from "node:path";
import { promisify as promisify2 } from "node:util";
import {
  ProcessLockRefused,
  deriveDeliveryRecordPath,
  resolveRecordStorage as resolveRecordStorage6,
  sha256Hex as sha256Hex5,
  withProcessLock
} from "./kernel.mjs";
var execFileAsync = promisify2(execFile2);
var SCOPE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
var DIGEST = /^[a-f0-9]{64}$/;
var LEDGER_VERSION = "delivery-record-retention/1";
var RETENTION_LEAF = "delivery-record-retention";
var failure = (code, detail) => ({ ok: false, code, detail });
var scopeDigest = (scope) => createHash("sha256").update(scope, "utf8").digest("hex");
function isRecord(value2) {
  return typeof value2 === "object" && value2 !== null && !Array.isArray(value2);
}
function receipt(value2, recordBasePath) {
  if (!isRecord(value2) || Object.keys(value2).sort().join(",") !== "deliverableDigest,relativePath,sha256") return void 0;
  const relativePath = value2["relativePath"];
  const deliverableDigest = value2["deliverableDigest"];
  const sha256 = value2["sha256"];
  if (typeof relativePath !== "string" || typeof deliverableDigest !== "string" || !DIGEST.test(deliverableDigest) || typeof sha256 !== "string" || !DIGEST.test(sha256) || deriveDeliveryRecordPath(recordBasePath, deliverableDigest) !== relativePath) return void 0;
  return { relativePath, deliverableDigest, sha256 };
}
function parseLedger(value2, expectedName) {
  if (!isRecord(value2) || Object.keys(value2).sort().join(",") !== "owned,pendingPrune,recordBasePath,scope,version" || value2["version"] !== LEDGER_VERSION || typeof value2["scope"] !== "string" || !SCOPE.test(value2["scope"]) || typeof value2["recordBasePath"] !== "string" || !Array.isArray(value2["owned"]) || !Array.isArray(value2["pendingPrune"]) || `${scopeDigest(value2["scope"])}.json` !== expectedName) return void 0;
  const owned = value2["owned"].map((entry) => receipt(entry, value2["recordBasePath"]));
  const pendingPrune = value2["pendingPrune"].map((entry) => receipt(entry, value2["recordBasePath"]));
  if (owned.some((entry) => entry === void 0) || pendingPrune.some((entry) => entry === void 0)) return void 0;
  const paths = [...owned, ...pendingPrune].map((entry) => entry.relativePath);
  if (new Set(paths).size !== paths.length) return void 0;
  return {
    version: LEDGER_VERSION,
    scope: value2["scope"],
    recordBasePath: value2["recordBasePath"],
    owned,
    pendingPrune
  };
}
async function readLedgers(storageDir) {
  const ledgers = /* @__PURE__ */ new Map();
  for (const name of await readdir3(storageDir).catch((error) => {
    if (error.code === "ENOENT") return [];
    throw error;
  })) {
    if (!name.endsWith(".json")) continue;
    const ledgerPath = path12.join(storageDir, name);
    const stats = await lstat2(ledgerPath).catch(() => void 0);
    if (stats === void 0 || !stats.isFile() || stats.isSymbolicLink() || (stats.mode & 63) !== 0) {
      return failure("retention_ledger_unsafe", `${ledgerPath} is not an owner-only regular file`);
    }
    let value2;
    try {
      value2 = JSON.parse(await readFile4(ledgerPath, "utf8"));
    } catch {
      return failure("retention_ledger_invalid", `${ledgerPath} is not valid JSON`);
    }
    const parsed = parseLedger(value2, name);
    if (parsed === void 0) return failure("retention_ledger_invalid", `${ledgerPath} has an unsupported or inconsistent shape`);
    ledgers.set(parsed.scope, parsed);
  }
  const owners = /* @__PURE__ */ new Map();
  for (const ledger of ledgers.values()) {
    for (const entry of [...ledger.owned, ...ledger.pendingPrune]) {
      const prior = owners.get(entry.relativePath);
      if (prior !== void 0 && prior !== ledger.scope) {
        return failure("retention_ownership_ambiguous", `${entry.relativePath} is claimed by ${prior} and ${ledger.scope}`);
      }
      owners.set(entry.relativePath, ledger.scope);
    }
  }
  return { ok: true, ledgers };
}
async function writeLedger(ledgerPath, ledger) {
  await mkdir5(path12.dirname(ledgerPath), { recursive: true, mode: 448 });
  await chmod2(path12.dirname(ledgerPath), 448);
  const temporary = `${ledgerPath}.tmp-${randomUUID8()}`;
  const handle = await open(temporary, fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL, 384);
  try {
    await handle.writeFile(`${JSON.stringify(ledger)}
`, "utf8");
    await handle.sync();
    await handle.chmod(384);
  } finally {
    await handle.close();
  }
  await rename3(temporary, ledgerPath);
}
async function verifyWorkingRecord(rootDir, entry) {
  const absolute = path12.join(rootDir, entry.relativePath);
  const stats = await lstat2(absolute).catch((error) => error.code === "ENOENT" ? void 0 : Promise.reject(error));
  if (stats === void 0) return failure("retention_owned_record_changed", `${entry.relativePath} is missing`);
  if (!stats.isFile() || stats.isSymbolicLink()) return failure("retention_owned_record_unsafe", `${entry.relativePath} is not a regular file`);
  if (sha256Hex5(await readFile4(absolute)) !== entry.sha256) {
    return failure("retention_owned_record_changed", `${entry.relativePath} no longer matches its ownership receipt`);
  }
  return { ok: true };
}
async function gitCarriesExactRecord(rootDir, entry) {
  try {
    const { stdout: listing } = await execFileAsync("git", ["ls-tree", "HEAD", "--", entry.relativePath], { cwd: rootDir, encoding: "utf8" });
    if (!listing.startsWith("100644 blob ") || !listing.endsWith(`	${entry.relativePath}
`)) return false;
    const { stdout } = await execFileAsync("git", ["show", `HEAD:${entry.relativePath}`], { cwd: rootDir, encoding: "buffer", maxBuffer: 20 * 1024 * 1024 });
    return sha256Hex5(stdout) === entry.sha256;
  } catch {
    return false;
  }
}
async function applyDeliveryRecordRetention(input) {
  if (!SCOPE.test(input.scope)) return failure("retention_scope_invalid", "retention scope must be a plain 1-128 character key");
  if (!Number.isSafeInteger(input.keepSuperseded) || input.keepSuperseded < 0 || input.keepSuperseded > 100) {
    return failure("retention_bound_invalid", "keepSuperseded must be an integer from 0 through 100");
  }
  if (!DIGEST.test(input.current.deliverableDigest) || deriveDeliveryRecordPath(input.recordBasePath, input.current.deliverableDigest) !== input.current.relativePath) {
    return failure("retention_ledger_invalid", "the current path is not the configured per-digest record path");
  }
  const storage = await resolveRecordStorage6(input.rootDir, { storageNamespace: input.storageNamespace, leaf: RETENTION_LEAF });
  const ledgerPath = path12.join(storage.storageDir, `${scopeDigest(input.scope)}.json`);
  const lockPath = path12.join(storage.storageDir, ".ownership.lock");
  await mkdir5(storage.storageDir, { recursive: true, mode: 448 });
  try {
    return await withProcessLock(lockPath, 5e3, async () => {
      const loaded = await readLedgers(storage.storageDir);
      if (!loaded.ok) return loaded;
      const existing = loaded.ledgers.get(input.scope);
      if (existing !== void 0 && existing.recordBasePath !== input.recordBasePath) {
        return failure("retention_ledger_invalid", `retention scope ${input.scope} was created for a different delivery record path`);
      }
      let ledger = existing ?? {
        version: LEDGER_VERSION,
        scope: input.scope,
        recordBasePath: input.recordBasePath,
        owned: [],
        pendingPrune: []
      };
      const currentSha = sha256Hex5(input.current.bytes);
      const currentReceipt = {
        relativePath: input.current.relativePath,
        deliverableDigest: input.current.deliverableDigest,
        sha256: currentSha
      };
      const returningCurrent = ledger.pendingPrune.find((entry) => entry.relativePath === currentReceipt.relativePath);
      if (returningCurrent !== void 0) {
        if (returningCurrent.sha256 !== currentReceipt.sha256 || returningCurrent.deliverableDigest !== currentReceipt.deliverableDigest) {
          return failure("retention_owned_record_changed", `${currentReceipt.relativePath} conflicts with its pending ownership receipt`);
        }
        ledger = {
          ...ledger,
          owned: [...ledger.owned, returningCurrent],
          pendingPrune: ledger.pendingPrune.filter((entry) => entry.relativePath !== currentReceipt.relativePath)
        };
        await writeLedger(ledgerPath, ledger);
      }
      for (const pending of ledger.pendingPrune) {
        const absolute = path12.join(input.rootDir, pending.relativePath);
        const present = await lstat2(absolute).then(() => true, (error) => error.code === "ENOENT" ? false : Promise.reject(error));
        if (present) {
          const verified = await verifyWorkingRecord(input.rootDir, pending);
          if (!verified.ok) return verified;
          if (!await gitCarriesExactRecord(input.rootDir, pending)) {
            return failure("retention_history_missing", `${pending.relativePath} is not preserved byte-for-byte at HEAD`);
          }
          try {
            await unlink2(absolute);
          } catch (error) {
            return failure("retention_cleanup_failed", `could not remove ${pending.relativePath}: ${error instanceof Error ? error.message : String(error)}`);
          }
        }
      }
      if (ledger.pendingPrune.length > 0) {
        ledger = { ...ledger, pendingPrune: [] };
        await writeLedger(ledgerPath, ledger);
      }
      const currentIndex = ledger.owned.findIndex((entry) => entry.relativePath === currentReceipt.relativePath);
      if (currentIndex >= 0 && (ledger.owned[currentIndex].sha256 !== currentSha || ledger.owned[currentIndex].deliverableDigest !== currentReceipt.deliverableDigest)) {
        return failure("retention_owned_record_changed", `${currentReceipt.relativePath} conflicts with its ownership receipt`);
      }
      for (const other of loaded.ledgers.values()) {
        if (other.scope !== input.scope && [...other.owned, ...other.pendingPrune].some((entry) => entry.relativePath === currentReceipt.relativePath)) {
          return failure("retention_ownership_ambiguous", `${currentReceipt.relativePath} belongs to retention scope ${other.scope}`);
        }
      }
      const currentStats = await lstat2(path12.join(input.rootDir, currentReceipt.relativePath)).catch((error) => error.code === "ENOENT" ? void 0 : Promise.reject(error));
      if (currentIndex < 0 && currentStats !== void 0) {
        return failure("retention_path_unowned", `${currentReceipt.relativePath} existed before retention scope ${input.scope} owned it`);
      }
      for (const entry of ledger.owned) {
        if (entry.relativePath === currentReceipt.relativePath && currentStats === void 0) continue;
        const verified = await verifyWorkingRecord(input.rootDir, entry);
        if (!verified.ok) return verified;
      }
      const ordered = [
        ...ledger.owned.filter((entry) => entry.relativePath !== currentReceipt.relativePath),
        currentReceipt
      ];
      const retainCount = input.keepSuperseded + 1;
      const pendingPrune = ordered.slice(0, Math.max(0, ordered.length - retainCount));
      for (const entry of pendingPrune) {
        if (!await gitCarriesExactRecord(input.rootDir, entry)) {
          return failure("retention_history_missing", `${entry.relativePath} is not preserved byte-for-byte at HEAD`);
        }
      }
      ledger = { ...ledger, owned: ordered, pendingPrune: [] };
      await writeLedger(ledgerPath, ledger);
      if (currentStats === void 0) {
        try {
          await input.writeCurrent();
        } catch (error) {
          return failure("retention_write_failed", error instanceof Error ? error.message : String(error));
        }
      }
      const verifiedCurrent = await verifyWorkingRecord(input.rootDir, currentReceipt);
      if (!verifiedCurrent.ok) return verifiedCurrent;
      if (pendingPrune.length > 0) {
        const kept = ordered.slice(pendingPrune.length);
        ledger = { ...ledger, owned: kept, pendingPrune };
        await writeLedger(ledgerPath, ledger);
        for (const entry of pendingPrune) {
          try {
            await unlink2(path12.join(input.rootDir, entry.relativePath));
          } catch (error) {
            return failure("retention_cleanup_failed", `could not remove ${entry.relativePath}: ${error instanceof Error ? error.message : String(error)}`);
          }
        }
        ledger = { ...ledger, pendingPrune: [] };
        await writeLedger(ledgerPath, ledger);
      }
      return { ok: true, pruned: pendingPrune.map((entry) => entry.relativePath), ledgerPath };
    });
  } catch (error) {
    if (error instanceof ProcessLockRefused) {
      return failure("retention_lock_unavailable", `retention scope ${input.scope} is already being updated: ${error.reason}`);
    }
    throw error;
  }
}

// packages/cli/src/commands/record.ts
function parseRetentionOptions(args) {
  if (args.length === 0) return void 0;
  let scope;
  let keepSuperseded;
  for (let index = 0; index < args.length; index += 2) {
    const flag3 = args[index];
    const value2 = args[index + 1];
    if (value2 === void 0) return `${flag3 ?? "record option"} requires a value`;
    if (flag3 === "--retention-scope" && scope === void 0) scope = value2;
    else if (flag3 === "--keep-superseded" && keepSuperseded === void 0) {
      if (!/^\d+$/.test(value2)) return "--keep-superseded must be an integer from 0 through 100";
      keepSuperseded = Number(value2);
    } else return `${oneLine(flag3 ?? "record option", 64)} is not a valid record retention option`;
  }
  if (scope === void 0 || keepSuperseded === void 0) {
    return "--retention-scope and --keep-superseded must be supplied together";
  }
  if (!Number.isSafeInteger(keepSuperseded) || keepSuperseded > 100) {
    return "--keep-superseded must be an integer from 0 through 100";
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(scope)) {
    return "--retention-scope must be a plain 1-128 character delivery key";
  }
  return { scope, keepSuperseded };
}
async function writeRecord(context, retention) {
  const observedAt = `${(/* @__PURE__ */ new Date()).toISOString().slice(0, 19)}Z`;
  const wiring = await context.wire();
  const admission = await runProviderBackedAdmission(context, { allowPrompt: false, includeInjectedLiveResults: false });
  if (!admission.admitted || admission.decision === void 0) {
    return { kind: "blocked", blockers: [...admission.blockers] };
  }
  const decision = admission.decision;
  const recheck = await wiring.captureCandidate();
  if (!recheck.ok) {
    return { kind: "blocked", blockers: [...recheck.blockers] };
  }
  if (recheck.candidate.deliverable.digest !== decision.candidate.deliverable.digest) {
    return {
      kind: "blocked",
      blockers: [
        commandBlocker({
          code: "record_identity_changed",
          sourceId: "delivery-harness.cli.record",
          summary: "The deliverable identity changed after the gate; nothing was recorded.",
          details: `gate ${decision.candidate.deliverable.digest} but current ${recheck.candidate.deliverable.digest}`,
          remediations: [
            {
              id: "reprepare-and-record",
              kind: "command",
              command: ["delivery-harness", "prepare"],
              summary: "Re-prepare the candidate and re-run the gate before recording."
            }
          ]
        })
      ]
    };
  }
  const evidenceRecords = [];
  for (const obligation of context.config.obligations) {
    const discovery = await discoverRecords2(context.rootDir, {
      gateId: context.config.gateId,
      obligationId: obligation.id,
      ...wiring.storageOptions
    });
    evidenceRecords.push(...discovery.records);
  }
  const evidenceReader = repositoryEvidenceReader2(context.rootDir, context.artifacts);
  const evidenceContext = await capturePortableEvidenceContext2(
    context.config,
    evidenceReader,
    await computePreparationFingerprint2(context.rootDir, context.config),
    recheck.candidate.deliverable.digest
  );
  const compiledPolicy = context.policyBinding?.compiledPolicy ?? await readCompiledRepositoryPolicy(await candidateTreeEvidenceReader2(context.rootDir, recheck.candidate.treeSha));
  const runSpan = await resolveRunSpan({ cwd: context.rootDir, treeSha: recheck.candidate.treeSha, allowCurrentRun: true });
  const built = buildDeliveryRecord({
    config: context.config,
    decision,
    evidenceRecords,
    context: evidenceContext,
    ...runSpan === void 0 ? {} : { runSpan: { startedAt: runSpan.startedAt, endedAt: runSpan.endedAt } },
    ...compiledPolicy === null ? {} : { compiledPolicy, observedAt }
  });
  if (!built.ok) {
    return { kind: "blocked", blockers: [...built.blockers] };
  }
  const verificationInputs = await capturePortableVerificationInputs(context.rootDir, context.config, recheck.candidate, built.record);
  const checked = verifyDeliveryRecord(
    context.config,
    built.record,
    { deliverableDigest: recheck.candidate.deliverable.digest, identityToken: recheck.candidate.deliverable.identity },
    recheck.candidate.base,
    { ...verificationInputs, observedAt, liveResults: admission.observedLiveResults ?? [], executionContext: context.classifyContext() }
  );
  if (!checked.ok) return { kind: "blocked", blockers: [...checked.blockers] };
  const relativePath = deliveryRecordPathFor3(context.config, decision.candidate.deliverable.digest);
  const absolutePath = path13.join(context.rootDir, relativePath);
  const bytes = deliveryRecordBytes(built.record);
  if (retention === void 0) {
    await context.artifacts.writeTextFile(absolutePath, bytes);
    return { kind: "ok", summary: `recorded ${relativePath}` };
  }
  const retained = await applyDeliveryRecordRetention({
    rootDir: context.rootDir,
    storageNamespace: context.config.storageNamespace,
    scope: retention.scope,
    keepSuperseded: retention.keepSuperseded,
    recordBasePath: context.config.deliveryRecordPath,
    current: {
      relativePath,
      deliverableDigest: decision.candidate.deliverable.digest,
      bytes
    },
    writeCurrent: () => context.artifacts.writeTextFile(absolutePath, bytes)
  });
  if (!retained.ok) {
    return {
      kind: "blocked",
      blockers: [
        commandBlocker({
          code: retained.code,
          sourceId: "delivery-harness.cli.record",
          summary: "Tracked delivery-record retention did not complete.",
          details: retained.detail,
          remediations: [
            {
              id: "repair-record-retention",
              kind: "manual_action",
              summary: "Preserve the exact owned record bytes in Git, repair the named ownership conflict, then retry record with the same scope and bound."
            }
          ]
        })
      ]
    };
  }
  return {
    kind: "ok",
    summary: `recorded ${relativePath}${retained.pruned.length === 0 ? "" : `; pruned Git-preserved ${retained.pruned.join(", ")}`}`
  };
}
var recordCommand = {
  capturesCandidate: true,
  name: "record",
  sourceId: "delivery-harness.cli.record",
  summary: "Write the tracked delivery record for an admitted gate.",
  usage: "Usage: delivery-harness record [--retention-scope <delivery-key> --keep-superseded <0-100>]\nRetention is opt-in; run gate first if a waiver is needed.",
  async run(context) {
    const retention = parseRetentionOptions(context.args);
    if (typeof retention === "string") {
      return { kind: "usage", message: `${retention}.
${recordCommand.usage}` };
    }
    const focused = await focusedOutcome(context);
    if (focused !== void 0) return focused;
    return writeRecord(context, retention);
  }
};

// packages/cli/src/commands/review-context.ts
import { evaluatePreparationReceipt as evaluatePreparationReceipt3 } from "./kernel.mjs";
var USAGE3 = "Usage: delivery-harness review-context [--json]";
var reviewContextCommand = {
  capturesCandidate: true,
  name: "review-context",
  sourceId: "delivery-harness.cli.review-context",
  summary: "Show the reviewable-change context for the prepared candidate.",
  usage: USAGE3,
  async run(context) {
    if (context.args.length > 0 && !(context.args.length === 1 && context.args[0] === "--json")) {
      return { kind: "usage", message: `review-context accepts only --json.
${USAGE3}` };
    }
    const wiring = await context.wire();
    const capture2 = await wiring.captureCandidate();
    if (!capture2.ok) {
      return { kind: "blocked", blockers: [...capture2.blockers] };
    }
    const evaluation = await evaluatePreparationReceipt3(
      context.rootDir,
      { config: context.config, candidate: capture2.candidate },
      wiring.storageOptions
    );
    if (!evaluation.prepared) {
      const blockers = [...evaluation.blockers];
      if (evaluation.failure === "missing") {
        blockers.push(
          commandBlocker({
            code: "review_context_requires_receipt",
            sourceId: "delivery-harness.cli.review-context",
            summary: "Review context is unavailable until the candidate is prepared.",
            remediations: [
              {
                id: "run-prepare",
                kind: "command",
                command: ["delivery-harness", "prepare"],
                summary: "Publish a preparation receipt for the current candidate."
              }
            ]
          })
        );
      }
      return { kind: "blocked", blockers };
    }
    if (context.args[0] === "--json") {
      try {
        const document = await buildReviewContext(context.rootDir, context.config, capture2.candidate, evaluation.receipt);
        return { kind: "ok", summary: JSON.stringify(document, null, 2) };
      } catch (error) {
        if (!(error instanceof OutcomeError)) throw error;
        return { kind: "blocked", blockers: [commandBlocker({
          code: "review_context_unavailable",
          sourceId: "delivery-harness.cli.review-context",
          summary: error.message,
          remediations: [{ id: "restore-review-inputs", kind: "manual_action", summary: "Restore the installed workflow release and compiled policy charters before acquiring review." }]
        })] };
      }
    }
    const projection = await wiring.projectActivation(capture2.candidate);
    const active = projection.relevantLineCount >= context.config.activationThreshold || projection.hasRelevantBinaryChange;
    return {
      kind: "ok",
      summary: [
        `review context for ${context.config.gateId}:`,
        `  candidate tree ${capture2.candidate.treeSha} (${capture2.candidate.mode})`,
        `  relevant lines ${projection.relevantLineCount} across ${projection.changedEntryCount} changed entr${projection.changedEntryCount === 1 ? "y" : "ies"}`,
        `  activation ${active ? "active" : "inactive"} (threshold ${context.config.activationThreshold})`,
        ...projection.sensitivePathIds.length > 0 ? [`  sensitive: ${projection.sensitivePathIds.join(", ")}`] : [],
        `  submit evidence with: delivery-harness submit-evidence --manifest <path>`
      ].join("\n")
    };
  }
};

// packages/cli/src/commands/submit-evidence.ts
import { submitManifest as submitManifest4 } from "./kernel.mjs";
function manifestPathsFrom(args) {
  const paths = [];
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--manifest") {
      const value2 = args[++index];
      if (value2 === void 0 || value2 === "") return void 0;
      paths.push(value2);
    } else if (!token.startsWith("-")) paths.push(token);
  }
  return paths;
}
function unknownFlagIn(args) {
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--manifest") {
      index += 1;
      continue;
    }
    if (token.startsWith("-")) return token;
  }
  return void 0;
}
var submitEvidenceCommand = {
  capturesCandidate: true,
  name: "submit-evidence",
  sourceId: "delivery-harness.cli.submit-evidence",
  summary: "Validate a provider manifest and publish its evidence records.",
  usage: "Usage: delivery-harness submit-evidence --manifest <path>",
  async run(context) {
    const unknown = unknownFlagIn(context.args);
    if (unknown !== void 0) {
      return { kind: "usage", message: `Unknown flag ${oneLine(unknown, 64)}.
${submitEvidenceCommand.usage}` };
    }
    const paths = manifestPathsFrom(context.args);
    if (paths === void 0) {
      return { kind: "usage", message: "submit-evidence requires --manifest <path>." };
    }
    if (paths.length > 1) {
      return { kind: "usage", message: `submit-evidence requires one manifest path; received ${paths.map((value2) => oneLine(value2, 256)).join(", ")}.` };
    }
    const manifestPath = paths[0];
    if (manifestPath === void 0 || manifestPath === "") {
      return { kind: "usage", message: "submit-evidence requires --manifest <path>." };
    }
    const wiring = await context.wire();
    let session;
    let outcome2;
    try {
      if (context.config.providers.some((p) => p.check?.scope)) {
        const capture2 = await wiring.captureCandidate();
        if (capture2.ok) session = await ScopedChecks.create(context, capture2.candidate);
      }
      outcome2 = await submitManifest4(
        { rootDir: context.rootDir, manifestPath, config: context.config },
        {
          captureCandidate: wiring.captureCandidate,
          artifacts: context.artifacts,
          ...wiring.storageOptions,
          ...session ? { scopedPlan: await session.plan(), readOutput: session.readOutput } : {}
        }
      );
    } finally {
      await session?.cleanup();
    }
    if (outcome2.status === "accepted") {
      const lines = outcome2.records.map(
        (record4) => `  ${record4.obligationId}: ${record4.status} ${record4.recordId}`
      );
      return {
        kind: "ok",
        summary: [`accepted (manifestDigest ${outcome2.manifestDigest}):`, ...lines].join("\n")
      };
    }
    return { kind: "blocked", blockers: [...outcome2.blockers] };
  }
};

// packages/cli/src/commands/admit.ts
var USAGE4 = [
  "Usage: delivery-harness admit --outcome <review-outcome.json>",
  "Prepares the candidate, binds the concluded outcome to its exact current review context, submits it, gates it, and writes the tracked record.",
  "The command never runs reviewers or commits the record."
].join("\n");
function withArgs(context, args) {
  return { ...context, args };
}
function report(context, label2, result) {
  if (result.kind !== "ok") return result;
  if (result.summary !== void 0 && result.summary !== "") context.write(`${label2}: ${result.summary}`);
  return void 0;
}
function invalidOutcome(message) {
  return {
    kind: "blocked",
    blockers: [commandBlocker({
      code: "review_outcome_invalid",
      sourceId: "delivery-harness.cli.admit",
      summary: message,
      remediations: [{
        id: "supply-reviewed-current-context",
        kind: "manual_action",
        summary: "Prepare the candidate, retain review-context --json before independent review, and supply the concluded outcome naming that exact digest."
      }]
    })]
  };
}
var admitCommand = {
  capturesCandidate: true,
  name: "admit",
  sourceId: "delivery-harness.cli.admit",
  summary: "Prepare and admit a concluded review outcome through the tracked record write.",
  usage: USAGE4,
  async run(context) {
    if (context.args.length !== 2 || context.args[0] !== "--outcome" || !context.args[1] || context.args[1].startsWith("-")) {
      return { kind: "usage", message: `admit requires --outcome <review-outcome.json>.
${USAGE4}` };
    }
    let outcome2;
    try {
      outcome2 = JSON.parse(await readFile5(path14.resolve(context.rootDir, context.args[1]), "utf8"));
    } catch {
      return { kind: "usage", message: `The review outcome must be a readable JSON document.
${USAGE4}` };
    }
    const prepared = await prepareCommand.run(withArgs(context, []));
    const stoppedAfterPrepare = report(context, "prepared", prepared);
    if (stoppedAfterPrepare !== void 0) return stoppedAfterPrepare;
    const reviewed = await reviewContextCommand.run(withArgs(context, ["--json"]));
    if (reviewed.kind !== "ok") return reviewed;
    if (reviewed.summary === void 0) return invalidOutcome("the prepared candidate produced no review context");
    let originalContext;
    try {
      originalContext = JSON.parse(reviewed.summary);
    } catch {
      return invalidOutcome("the prepared candidate produced an unreadable review context");
    }
    let manifestPath;
    try {
      const emitted = await emitReviewEvidence(context, originalContext, outcome2);
      manifestPath = emitted.manifestPath;
    } catch (error) {
      if (!(error instanceof OutcomeError)) throw error;
      return invalidOutcome(error.message);
    }
    context.write(`review evidence: ${manifestPath}`);
    const submitted = await submitEvidenceCommand.run(withArgs(context, ["--manifest", manifestPath]));
    const stoppedAfterSubmission = report(context, "submitted", submitted);
    if (stoppedAfterSubmission !== void 0) return stoppedAfterSubmission;
    const gated = await runGate(withArgs(context, []));
    const stoppedAfterGate = report(context, "gate", gated);
    if (stoppedAfterGate !== void 0) return stoppedAfterGate;
    return writeRecord(withArgs(context, []));
  }
};

// packages/cli/src/commands/emit.ts
import {
  RUN_STORE_ID,
  reduceToProviderId
} from "./kernel.mjs";
var USAGE5 = "Usage: delivery-harness emit <kind> [--run <id>] [--json <payload>] [--force] [--version 1|2] [--event-id <id>]";
var PAYLOAD_USAGE = "emit needs a JSON payload: use --json <payload>, or omit --json only when piping JSON to stdin until EOF.";
function parseArgs(args) {
  let kind;
  let run;
  let json;
  let force = false;
  let version;
  let eventId;
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--force") {
      force = true;
      continue;
    }
    if (token === "--run" || token === "--json" || token === "--version" || token === "--event-id") {
      const value2 = args[index + 1];
      if (value2 === void 0) return { ok: false, message: `${token} needs a value.
${USAGE5}` };
      if (token === "--run") run = value2;
      else if (token === "--json") json = value2;
      else if (token === "--event-id") eventId = value2;
      else {
        if (value2 !== "1" && value2 !== "2") return { ok: false, message: `--version must be 1 or 2.
${USAGE5}` };
        version = value2 === "1" ? "run-event/1" : "run-event/2";
      }
      index += 1;
      continue;
    }
    if (token.startsWith("--")) return { ok: false, message: `Unknown flag ${oneLine(token, 64)}.
${USAGE5}` };
    if (kind !== void 0) return { ok: false, message: `emit takes one kind.
${USAGE5}` };
    kind = token;
  }
  if (kind === void 0) return { ok: false, message: `emit needs a kind.
${USAGE5}` };
  return { ok: true, args: {
    kind,
    force,
    ...run === void 0 ? {} : { run },
    ...json === void 0 ? {} : { json },
    ...version === void 0 ? {} : { version },
    ...eventId === void 0 ? {} : { eventId }
  } };
}
function parsePayload(text2) {
  const trimmed = text2.trim();
  if (trimmed.length === 0) return {};
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}
async function suppliedPayload(context, json) {
  if (json !== void 0) return { ok: true, payload: parsePayload(json) };
  if (context.stdinIsTTY === true) {
    return { ok: false, message: `${PAYLOAD_USAGE}
stdin is interactive, so emit will not wait for terminal EOF.
${USAGE5}` };
  }
  const text2 = await context.readStdin();
  if (text2.trim() === "") return { ok: false, message: `${PAYLOAD_USAGE}
${USAGE5}` };
  return { ok: true, payload: parsePayload(text2) };
}
var noRun = (details) => runSurfaceBlocker({
  code: "run_unresolvable",
  summary: "There is no current run to emit against.",
  details,
  remediation: {
    id: "start-a-run",
    summary: "Run `delivery-harness emit run.started` in this worktree, or name an existing run with --run."
  }
});
async function resolveRun(store, surface, named) {
  if (named !== void 0) {
    if (named.length > 128 || !RUN_STORE_ID.test(named)) return void 0;
    const read = await store.read(named);
    return read.ok ? named : void 0;
  }
  const current = await store.current(surface.worktreeKey);
  return current.ok ? current.runId : void 0;
}
var emitCommand = {
  name: "emit",
  sourceId: "delivery-harness.cli.emit",
  summary: "Append one run event to the current delivery run's journal.",
  usage: USAGE5,
  configFree: true,
  async run(context) {
    const parsed = parseArgs(context.args);
    if (!parsed.ok) return { kind: "usage", message: parsed.message };
    const { kind, run: named, force } = parsed.args;
    const resolved = await resolveRunSurface(context.rootDir);
    if (!resolved.ok) {
      return {
        kind: "blocked",
        blockers: [
          runSurfaceBlocker({
            code: "run_store_unresolvable",
            summary: "The run store could not be resolved.",
            details: oneLine(resolved.reason, 200),
            remediation: {
              id: "run-inside-a-repository",
              summary: "Run this command inside a git repository; the run store lives under its common directory."
            }
          })
        ]
      };
    }
    const surface = resolved.surface;
    const store = surface.store;
    if (kind === "run.started") {
      const version2 = parsed.args.version ?? "run-event/1";
      if (version2 === "run-event/2" !== (parsed.args.eventId !== void 0)) {
        return {
          kind: "usage",
          message: version2 === "run-event/2" ? `run.started is creating a version 2 run; every emit needs --event-id.
${USAGE5}` : `run.started is creating a version 1 run and does not accept --event-id; drop it.
${USAGE5}`
        };
      }
      const supplied2 = await suppliedPayload(context, parsed.args.json);
      if (!supplied2.ok) return { kind: "usage", message: supplied2.message };
      return startRun(surface, force, supplied2.payload, version2, parsed.args.eventId);
    }
    const runId = await resolveRun(store, surface, named);
    if (runId === void 0) {
      return {
        kind: "blocked",
        blockers: [
          noRun(
            named === void 0 ? "no current run is pointed at from this worktree, and no --run was given" : `--run ${oneLine(named, 128)} names no readable journal in this store`
          )
        ]
      };
    }
    const history = await store.read(runId);
    if (!history.ok) return { kind: "blocked", blockers: [noRun("the selected run is unreadable")] };
    const version = history.events[0]?.version ?? "run-event/1";
    if (parsed.args.version !== void 0 && parsed.args.version !== version) {
      return { kind: "usage", message: "A run's writer version cannot change. End it and start an explicitly linked successor run." };
    }
    if (version === "run-event/2" !== (parsed.args.eventId !== void 0)) {
      return {
        kind: "usage",
        message: version === "run-event/2" ? `run ${runId} is version 2; every emit needs --event-id.
${USAGE5}` : `run ${runId} is version 1 and does not accept --event-id; drop it, and drop the version-2 payload members roundId, bound, grace and reopensRoundId.
${USAGE5}`
      };
    }
    const supplied = await suppliedPayload(context, parsed.args.json);
    if (!supplied.ok) return { kind: "usage", message: supplied.message };
    const payload = supplied.payload;
    const event = buildRunEvent({
      runId,
      commonDir: surface.commonDir,
      kind,
      role: "executor",
      payload,
      version,
      ...parsed.args.eventId === void 0 ? {} : { eventId: parsed.args.eventId }
    });
    if (kind === "command.completed") {
      await store.noteRefusal(runId, event, {
        code: "unsupported_combination",
        pointer: "/kind",
        message: "command.completed is written by the CLI; emit may not write it"
      });
      return {
        kind: "blocked",
        blockers: [
          runSurfaceBlocker({
            code: "run_event_refused",
            summary: "command.completed is written by the CLI, never by emit.",
            details: `run ${runId}: the product's own commands append their completions; an executor reports a non-product gate with gate.reported`,
            remediation: {
              id: "use-gate-reported",
              summary: "Emit gate.reported when the repository's gate is not a product command."
            }
          })
        ]
      };
    }
    const appended = await store.append(runId, event, { reuseExistingTimestamp: true });
    if (!appended.ok) {
      const first = appended.rejections[0];
      return {
        kind: "blocked",
        blockers: [
          runSurfaceBlocker({
            code: "run_event_refused",
            // The rejected kind is echoed reduced to the bound the note records
            // it at, so the diagnostic and the durable line say the same thing
            // and neither can carry an escape sequence to the terminal.
            summary: `The run event was refused: ${reduceToProviderId(kind)}`,
            details: `run ${runId}: ${first === void 0 ? "the store refused the append" : `${first.code} at ${oneLine(first.pointer, 64) || "/"}: ${oneLine(first.message, 200)}`}`,
            remediation: {
              id: "correct-the-event",
              summary: `Correct the kind or the payload against the ${version} contract and emit again.`
            }
          })
        ]
      };
    }
    if (kind === "run.ended") await store.clearCurrent(surface.worktreeKey, runId);
    return { kind: "ok", summary: `emitted ${appended.event.kind} seq ${appended.event.seq} to run ${runId}` };
  }
};
async function startRun(surface, force, supplied, version, eventId) {
  const store = surface.store;
  const existing = await store.current(surface.worktreeKey);
  const displaced = existing.ok ? existing.runId : void 0;
  if (displaced !== void 0 && !force) {
    return {
      kind: "blocked",
      blockers: [
        runSurfaceBlocker({
          code: "run_already_current",
          summary: "A run is already current for this worktree.",
          details: `run ${displaced} is current; --force displaces it and records it as displacedRunId`,
          remediation: {
            id: "end-or-force",
            summary: "End the current run with `emit run.ended`, or start this one with --force."
          }
        })
      ]
    };
  }
  const allocated = await store.allocate();
  if (!allocated.ok) {
    return {
      kind: "blocked",
      blockers: [
        runSurfaceBlocker({
          code: "run_not_allocated",
          summary: "A run journal could not be allocated.",
          details: oneLine(allocated.rejections[0]?.message ?? "the store refused to allocate", 200),
          remediation: { id: "check-the-store", summary: "Check that the repository's git common directory is writable." }
        })
      ]
    };
  }
  const runId = allocated.runId;
  const payload = typeof supplied === "object" && supplied !== null && displaced !== void 0 ? { ...supplied, displacedRunId: displaced } : supplied;
  const appended = await store.append(
    runId,
    buildRunEvent({
      runId,
      commonDir: surface.commonDir,
      kind: "run.started",
      role: "executor",
      payload,
      version,
      ...eventId === void 0 ? {} : { eventId }
    })
  );
  if (!appended.ok) {
    const first = appended.rejections[0];
    await store.discard(runId);
    return {
      kind: "blocked",
      blockers: [
        runSurfaceBlocker({
          code: "run_event_refused",
          summary: "The run event was refused: run.started",
          details: `run ${runId}: ${first === void 0 ? "the store refused the append" : `${first.code} at ${oneLine(first.pointer, 64) || "/"}: ${oneLine(first.message, 200)}`}`,
          remediation: {
            id: "correct-the-event",
            summary: `Correct the payload against the ${version} contract and emit again.`
          }
        })
      ]
    };
  }
  let pointed = await store.setCurrent(surface.worktreeKey, runId, { force });
  let displacedStale = false;
  if (!pointed.ok && !force) {
    const recheck = await store.current(surface.worktreeKey);
    if (recheck.ok && recheck.runId === void 0) {
      pointed = await store.setCurrent(surface.worktreeKey, runId, { force: true });
      displacedStale = pointed.ok;
    }
  }
  if (!pointed.ok) {
    await store.discard(runId);
    return {
      kind: "blocked",
      blockers: [
        runSurfaceBlocker({
          code: "run_pointer_refused",
          summary: "The worktree pointer could not be written.",
          details: `run ${runId}: ${oneLine(pointed.rejections[0]?.message ?? "the pointer was refused", 200)}`,
          // `--run` cannot help here: `run.started` allocates rather than
          // resolves, so it never reads that flag. `--force` is the one thing
          // that displaces a pointer this command refused to overwrite.
          remediation: { id: "force-the-start", summary: "Start the run with --force to displace the pointer that is already current." }
        })
      ]
    };
  }
  return {
    kind: "ok",
    summary: `started run ${runId}${displaced === void 0 ? displacedStale ? " (displaced a stale pointer naming no readable run; the run store may need attention)" : "" : ` (displaced ${displaced})`}`
  };
}

// packages/cli/src/commands/maintain.ts
import { execFile as execFile3 } from "node:child_process";
import { readFile as readFile6 } from "node:fs/promises";
import path15 from "node:path";
import { compiledAdopterPolicyBindingDigest, createManagedDeliveryFacade } from "./kernel.mjs";
var SOURCE_ID = "delivery-harness.cli.maintain";
var MAINTAIN_OPERATIONS = Object.freeze([
  "update",
  "rollback",
  "pin",
  "revoke",
  "unrevoke",
  "advance-high-water-mark"
]);
var blocked = (code, summary, remediation) => ({
  kind: "blocked",
  blockers: [
    commandBlocker({
      code,
      sourceId: SOURCE_ID,
      summary,
      remediations: [{ id: `${code.replaceAll("_", "-")}-remediation`, kind: "manual_action", summary: remediation }]
    })
  ]
});
var gitCommonDir = (cwd) => new Promise((resolve) => {
  execFile3("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, encoding: "utf8" }, (error, stdout) => {
    resolve(error === null ? stdout.trim() : void 0);
  });
});
var flag = (args, name) => {
  const index = args.indexOf(name);
  return index === -1 ? void 0 : args[index + 1];
};
function nowInstant() {
  return `${(/* @__PURE__ */ new Date()).toISOString().slice(0, 19)}Z`;
}
async function resolveFacade(context) {
  const common = await gitCommonDir(context.rootDir);
  if (common === void 0) {
    return blocked("not_a_repository", "The working directory is not a git repository.", "Run from the repository the product is installed for.");
  }
  let pointer;
  try {
    pointer = JSON.parse(await readFile6(path15.join(common, "managed-delivery", "facade.json"), "utf8"));
  } catch {
    return blocked(
      "no_managed_installation",
      "No managed installation is registered for this repository.",
      "Install the composition and register a delivery first."
    );
  }
  let policyBinding = context.policyBinding;
  if (policyBinding === void 0) {
    try {
      policyBinding = JSON.parse(await readFile6(path15.join(common, "managed-delivery", "policy-binding.json"), "utf8"));
    } catch {
      return blocked(
        "policy_binding_missing",
        "No compiled adopter policy binding is retained for this installation.",
        "Register a delivery through an adopter binding, or pass one through the embedding runtime."
      );
    }
  }
  if (pointer.policyBindingDigest !== compiledAdopterPolicyBindingDigest(policyBinding)) {
    return blocked(
      "policy_binding_mismatch",
      "The retained compiled adopter policy binding does not match the product namespace pointer.",
      "Restore the exact binding captured at registration; drift requires a new owner-approved delivery."
    );
  }
  return createManagedDeliveryFacade({
    repoDir: context.rootDir,
    policyBinding,
    installation: { installationPath: pointer.installationPath, receiptDir: pointer.receiptDir },
    hostVersion: pointer.hostVersion
  });
}
var isCommandResult = (value2) => "kind" in value2;
var maintainCommand = {
  name: "maintain",
  sourceId: SOURCE_ID,
  summary: "Maintain the product installation (update, rollback, trust-state pin/revoke/unrevoke/high-water-mark).",
  usage: `Usage: delivery-harness maintain <operation> [options]
Operations: ${MAINTAIN_OPERATIONS.join(" | ")}`,
  async run(context) {
    const [operation, ...rest] = context.args;
    if (operation === void 0) {
      return { kind: "usage", message: `maintain requires an operation: ${MAINTAIN_OPERATIONS.join(" | ")}` };
    }
    if (!MAINTAIN_OPERATIONS.includes(operation)) {
      return { kind: "usage", message: `Unknown maintain operation: ${operation}.` };
    }
    const resolved = await resolveFacade(context);
    if (isCommandResult(resolved)) return resolved;
    const facade = resolved;
    const now = nowInstant();
    const emit = (value2) => context.write(`${JSON.stringify(value2, null, 2)}
`);
    switch (operation) {
      case "update": {
        const packedDir = flag(rest, "--packed");
        if (packedDir === void 0) {
          return { kind: "usage", message: "update requires --packed <dir> naming the verified packed generation to install." };
        }
        const outcome2 = await facade.updateComposition({ packedDir: path15.resolve(context.rootDir, packedDir), now });
        if (!outcome2.ok) return { kind: "blocked", blockers: [...outcome2.blockers] };
        emit(outcome2);
        return {
          kind: "ok",
          summary: outcome2.noOp ? `already at ${outcome2.generationDigest}` : `updated ${outcome2.priorGenerationDigest} -> ${outcome2.generationDigest}`
        };
      }
      case "rollback": {
        const target = flag(rest, "--generation");
        if (target === void 0) {
          return { kind: "usage", message: "rollback requires --generation <digest> naming a previously accepted generation." };
        }
        const outcome2 = await facade.rollbackComposition({ targetGenerationDigest: target, now });
        if (!outcome2.ok) return { kind: "blocked", blockers: [...outcome2.blockers] };
        return { kind: "ok", summary: `rolled back to ${outcome2.generationDigest}` };
      }
      case "pin":
      case "revoke":
      case "unrevoke": {
        const generationDigest = flag(rest, "--generation");
        if (generationDigest === void 0) {
          return { kind: "usage", message: `${operation} requires --generation <digest>.` };
        }
        const outcome2 = await facade.maintainTrustState({ operation, generationDigest, now });
        if (!outcome2.ok) return { kind: "blocked", blockers: [...outcome2.blockers] };
        emit(outcome2.state);
        return { kind: "ok", summary: `${operation} recorded at revocation epoch ${outcome2.state.revocationEpoch}` };
      }
      case "advance-high-water-mark": {
        const raw = flag(rest, "--to");
        const highWaterMark = raw === void 0 ? Number.NaN : Number.parseInt(raw, 10);
        if (!Number.isInteger(highWaterMark)) {
          return { kind: "usage", message: "advance-high-water-mark requires --to <integer>." };
        }
        const outcome2 = await facade.maintainTrustState({ operation: "advance-high-water-mark", highWaterMark, now });
        if (!outcome2.ok) return { kind: "blocked", blockers: [...outcome2.blockers] };
        emit(outcome2.state);
        return { kind: "ok", summary: `high-water mark is ${outcome2.state.highWaterMark}` };
      }
      default:
        return { kind: "usage", message: `Unknown maintain operation: ${operation}.` };
    }
  }
};

// packages/cli/src/commands/managed.ts
import { execFile as execFile4 } from "node:child_process";
import { readFile as readFile7, readdir as readdir4, realpath as realpath3 } from "node:fs/promises";
import path16 from "node:path";
import {
  FACADE_OPERATIONS,
  compiledAdopterPolicyBindingDigest as compiledAdopterPolicyBindingDigest2,
  createManagedDeliveryFacade as createManagedDeliveryFacade2
} from "./kernel.mjs";
var SOURCE_ID2 = "delivery-harness.cli.managed";
var MANAGED_OPERATIONS = Object.freeze([
  "status",
  "deliveries",
  "next",
  "operations",
  "blockers",
  "explain-blocker",
  "submit-plan",
  "checkpoint",
  "run-sensor",
  "reduce-review",
  "compound",
  "admit",
  "prepare-record",
  "confirm-record",
  "finish",
  "propose-approval",
  "request-cancellation",
  "finalize-cancellation",
  "recover",
  "export",
  "delete"
]);
var blocked2 = (code, summary, remediation) => ({
  kind: "blocked",
  blockers: [
    commandBlocker({
      code,
      sourceId: SOURCE_ID2,
      summary,
      remediations: [{ id: `${code.replaceAll("_", "-")}-remediation`, kind: "manual_action", summary: remediation }]
    })
  ]
});
var gitCommonDir2 = (cwd) => new Promise((resolve) => {
  execFile4("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, encoding: "utf8" }, (error, stdout) => {
    resolve(error === null ? stdout.trim() : void 0);
  });
});
function nowInstant2() {
  return `${(/* @__PURE__ */ new Date()).toISOString().slice(0, 19)}Z`;
}
var flag2 = (args, name) => {
  const index = args.indexOf(name);
  return index === -1 ? void 0 : args[index + 1];
};
async function resolveInstallation(context) {
  const common = await gitCommonDir2(context.rootDir);
  if (common === void 0) {
    return blocked2("not_a_repository", "The working directory is not a git repository.", "Run from the delivery worktree.");
  }
  const namespace = path16.join(common, "managed-delivery");
  let pointer;
  try {
    pointer = JSON.parse(await readFile7(path16.join(namespace, "facade.json"), "utf8"));
  } catch {
    return blocked2(
      "no_managed_delivery",
      "No managed delivery is registered for this repository.",
      "Register a delivery through the facade's contract handoff first."
    );
  }
  let deliveries;
  try {
    deliveries = (await readdir4(path16.join(namespace, "deliveries"), { withFileTypes: true })).filter((candidate) => candidate.isDirectory()).map((candidate) => candidate.name).sort();
  } catch {
    deliveries = [];
  }
  const active = [];
  for (const candidate of deliveries) {
    try {
      const journal = await readFile7(path16.join(namespace, "deliveries", candidate, "journal.jsonl"), "utf8");
      const terminal = journal.split("\n").filter((line) => line.length > 0).some((line) => {
        try {
          const entry = JSON.parse(line);
          return entry.kind === "transition.committed" && ["completed", "cancelled", "failed"].includes(entry.payload?.to ?? "");
        } catch {
          return false;
        }
      });
      if (!terminal) active.push(candidate);
    } catch {
      active.push(candidate);
    }
  }
  let policyBinding = context.policyBinding;
  if (policyBinding === void 0) {
    try {
      policyBinding = JSON.parse(await readFile7(path16.join(namespace, "policy-binding.json"), "utf8"));
    } catch {
      return blocked2(
        "policy_binding_missing",
        "No compiled adopter policy binding is retained for this delivery.",
        "Register the delivery through an adopter binding, or pass one through the embedding runtime."
      );
    }
  }
  if (pointer.policyBindingDigest !== compiledAdopterPolicyBindingDigest2(policyBinding)) {
    return blocked2(
      "policy_binding_mismatch",
      "The retained compiled adopter policy binding does not match the product namespace pointer.",
      "Restore the exact binding captured at registration; drift requires a new owner-approved delivery."
    );
  }
  const facade = createManagedDeliveryFacade2({
    repoDir: context.rootDir,
    policyBinding,
    installation: { installationPath: pointer.installationPath, receiptDir: pointer.receiptDir },
    hostVersion: pointer.hostVersion
  });
  return { facade, namespace, deliveries, active };
}
async function resolveManaged(context, requested) {
  const installation = await resolveInstallation(context);
  if (isCommandResult2(installation)) return installation;
  const { facade, namespace, deliveries, active } = installation;
  let deliveryId;
  if (requested !== void 0) {
    if (!deliveries.includes(requested)) {
      return blocked2(
        "delivery_unresolved",
        `No delivery ${requested} is registered for this repository.`,
        "Name a delivery this repository registered; `managed status` reports the current one."
      );
    }
    deliveryId = requested;
  } else {
    deliveryId = active[0] ?? deliveries[deliveries.length - 1];
    if (deliveryId === void 0 || active.length > 1) {
      return blocked2(
        "delivery_unresolved",
        active.length > 1 ? "Several deliveries are in flight; the skeleton drives one." : "No registered delivery exists.",
        "Register exactly one delivery for this repository, or name one with --delivery for export and delete, or list them all with `managed deliveries`."
      );
    }
  }
  let fence;
  try {
    const workspace = JSON.parse(
      await readFile7(path16.join(namespace, "deliveries", deliveryId, "workspace.json"), "utf8")
    );
    if (typeof workspace.worktreeDir === "string" && typeof workspace.fence === "number") {
      const [boundReal, hereReal] = await Promise.all([realpath3(workspace.worktreeDir), realpath3(context.rootDir)]);
      if (boundReal === hereReal) fence = workspace.fence;
    }
  } catch {
    fence = void 0;
  }
  return { facade, deliveryId, fence };
}
var isCommandResult2 = (value2) => "kind" in value2;
var managedCommand = {
  name: "managed",
  sourceId: SOURCE_ID2,
  summary: "Drive the managed delivery's next checkpoint (status, stages, sensor, review, admission, record, finish).",
  usage: `Usage: delivery-harness managed <operation> [options]
Operations: ${MANAGED_OPERATIONS.join(" | ")}`,
  async run(context) {
    const [operation, ...rest] = context.args;
    if (operation === void 0) {
      return {
        kind: "usage",
        message: `managed requires an operation: ${MANAGED_OPERATIONS.join(" | ")}`
      };
    }
    if (!MANAGED_OPERATIONS.includes(operation)) {
      return { kind: "usage", message: `Unknown managed operation: ${operation}.` };
    }
    if (operation === "operations") {
      context.write(`${JSON.stringify(FACADE_OPERATIONS, null, 2)}
`);
      return { kind: "ok", summary: `${FACADE_OPERATIONS.length} facade operations` };
    }
    if (operation === "deliveries") {
      const installation = await resolveInstallation(context);
      if (isCommandResult2(installation)) return installation;
      const listing = await installation.facade.listDeliveries({ observedAt: nowInstant2() });
      if (!listing.ok) return { kind: "blocked", blockers: [...listing.blockers] };
      context.write(`${JSON.stringify(listing.deliveries, null, 2)}
`);
      const unreadable = listing.unreadable.length === 0 ? "" : `; ${listing.unreadable.length} unreadable (${listing.unreadable.join(", ")})`;
      return {
        kind: "ok",
        summary: `${listing.deliveries.length} delivery(ies) registered for this installation${unreadable}`
      };
    }
    const RETENTION_OPERATIONS = ["export", "delete"];
    if (rest.some((argument) => argument.startsWith("--delivery="))) {
      return { kind: "usage", message: "--delivery takes its value as a separate argument: --delivery <id>." };
    }
    const namesDelivery = rest.includes("--delivery");
    if (namesDelivery && !RETENTION_OPERATIONS.includes(operation)) {
      return { kind: "usage", message: `--delivery is accepted only by: ${RETENTION_OPERATIONS.join(", ")}.` };
    }
    const requestedDelivery = RETENTION_OPERATIONS.includes(operation) ? flag2(rest, "--delivery") : void 0;
    if (namesDelivery && requestedDelivery === void 0) {
      return { kind: "usage", message: "--delivery requires a delivery id." };
    }
    const resolved = await resolveManaged(context, requestedDelivery);
    if (isCommandResult2(resolved)) return resolved;
    const { facade, deliveryId, fence } = resolved;
    const requireFence = () => fence ?? blocked2(
      "workspace_superseded",
      "This worktree is not the delivery's currently bound workspace, so it carries no invocation fence.",
      "Drive checkpoints from the bound worktree; a superseded task's outputs are permanently rejected."
    );
    const emit = (value2) => context.write(`${JSON.stringify(value2, null, 2)}
`);
    const resultFileArg = async (operation2) => {
      const file = flag2(rest, "--result-file");
      if (file === void 0) {
        return {
          kind: "usage",
          message: `${operation2} requires --result-file <path> containing a typed workflow-stage-result/1 document; prose cannot advance a checkpoint.`
        };
      }
      try {
        return await readFile7(path16.resolve(context.rootDir, file), "utf8");
      } catch (error) {
        return { kind: "usage", message: `${operation2} could not read ${file}: ${error instanceof Error ? error.message : String(error)}` };
      }
    };
    switch (operation) {
      case "status": {
        const status = await facade.status({ deliveryId, observedAt: nowInstant2() });
        if (!status.ok) return { kind: "blocked", blockers: [...status.blockers] };
        emit(status.status);
        return {
          kind: "ok",
          summary: `state ${status.status.delivery.state}; host ${status.status.hostActivity}; next ${status.status.nextCheckpoint.kind}`
        };
      }
      case "blockers": {
        const inventory = await facade.blockerInventory({ deliveryId });
        if (!inventory.ok) return { kind: "blocked", blockers: [...inventory.blockers] };
        emit(inventory.entries);
        return { kind: "ok", summary: `${inventory.entries.length} blocker(s) journaled` };
      }
      case "propose-approval": {
        const requestKind = flag2(rest, "--kind");
        const criterionId = flag2(rest, "--criterion");
        const actorId = flag2(rest, "--actor");
        const reason = flag2(rest, "--reason");
        if (requestKind !== "waiver" && requestKind !== "amendment" || criterionId === void 0 || actorId === void 0 || reason === void 0) {
          return {
            kind: "usage",
            message: "propose-approval requires --kind <waiver|amendment> --criterion <id> --actor <id> --reason <text>."
          };
        }
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const proposed = await facade.recordApprovalRequest({
          deliveryId,
          requestKind,
          criterionId,
          actorId,
          reason,
          fence: invokingFence
        });
        if (!proposed.ok) return { kind: "blocked", blockers: [...proposed.blockers] };
        return { kind: "ok", summary: `${requestKind} proposed for ${criterionId}; delivery is ${proposed.state}` };
      }
      case "request-cancellation": {
        const requested = await facade.requestCancellation({ deliveryId });
        if (!requested.ok) return { kind: "blocked", blockers: [...requested.blockers] };
        return { kind: "ok", summary: `cancellation requested; delivery is ${requested.state}` };
      }
      case "finalize-cancellation": {
        const finalized = await facade.finalizeCancellation({ deliveryId });
        if (!finalized.ok) return { kind: "blocked", blockers: [...finalized.blockers] };
        return { kind: "ok", summary: `prior workspace quarantined; delivery is ${finalized.state}` };
      }
      case "export": {
        const exported = await facade.exportDelivery({ deliveryId });
        if (!exported.ok) return { kind: "blocked", blockers: [...exported.blockers] };
        emit({ exportPath: exported.exportPath, artifactDigest: exported.artifactDigest });
        return { kind: "ok", summary: `exported to ${exported.exportPath}` };
      }
      case "delete": {
        const deleted = await facade.deleteDelivery({ deliveryId });
        if (!deleted.ok) return { kind: "blocked", blockers: [...deleted.blockers] };
        emit({ preservedAuditRecords: deleted.preservedAuditRecords });
        return { kind: "ok", summary: `deleted; ${deleted.preservedAuditRecords.length} audit record(s) preserved` };
      }
      case "recover": {
        const target = flag2(rest, "--generation");
        const recovered = await facade.recoverSecurityBlocked({
          deliveryId,
          now: nowInstant2(),
          ...target === void 0 ? {} : { targetGenerationDigest: target }
        });
        if (!recovered.ok) return { kind: "blocked", blockers: [...recovered.blockers] };
        return { kind: "ok", summary: `${recovered.mode}; delivery is ${recovered.state}` };
      }
      case "next": {
        const next = await facade.nextCheckpoint({ deliveryId });
        if (!next.ok) return { kind: "blocked", blockers: [...next.blockers] };
        emit(next.checkpoint);
        return { kind: "ok", summary: `next checkpoint: ${next.checkpoint.kind}` };
      }
      case "submit-plan": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const resultBytes = await resultFileArg("submit-plan");
        if (typeof resultBytes !== "string") return resultBytes;
        const submitted = await facade.submitStageResult({ deliveryId, stageId: "plan", resultBytes, fence: invokingFence });
        if (!submitted.ok) return { kind: "blocked", blockers: [...submitted.blockers] };
        return { kind: "ok", summary: `plan accepted; delivery is ${submitted.state}` };
      }
      case "checkpoint": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const resultBytes = await resultFileArg("checkpoint");
        if (typeof resultBytes !== "string") return resultBytes;
        const checkpointed = await facade.checkpointCandidate({ deliveryId, resultBytes, fence: invokingFence });
        if (!checkpointed.ok) return { kind: "blocked", blockers: [...checkpointed.blockers] };
        return { kind: "ok", summary: `candidate ${checkpointed.treeSha} checkpointed; delivery is ${checkpointed.state}` };
      }
      case "run-sensor": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const sensed = await facade.runSensor({ deliveryId, fence: invokingFence });
        if (!sensed.ok) return { kind: "blocked", blockers: [...sensed.blockers] };
        return { kind: "ok", summary: `sensor ${sensed.outcome}; delivery is ${sensed.state}` };
      }
      case "reduce-review": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const reduced = await facade.reduceReview({ deliveryId, fence: invokingFence });
        if (!reduced.ok) return { kind: "blocked", blockers: [...reduced.blockers] };
        return { kind: "ok", summary: `review reduced; delivery is ${reduced.state}` };
      }
      case "compound": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const resultBytes = await resultFileArg("compound");
        if (typeof resultBytes !== "string") return resultBytes;
        const compounded = await facade.submitStageResult({ deliveryId, stageId: "compound", resultBytes, fence: invokingFence });
        if (!compounded.ok) return { kind: "blocked", blockers: [...compounded.blockers] };
        return { kind: "ok", summary: `compound recorded; delivery is ${compounded.state}` };
      }
      case "admit": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const admitted = await facade.admit({ deliveryId, recordedAtInstant: nowInstant2(), env: context.env, fence: invokingFence });
        if (!admitted.ok) return { kind: "blocked", blockers: [...admitted.blockers] };
        return { kind: "ok", summary: `admitted; delivery is ${admitted.state}` };
      }
      case "prepare-record": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const prepared = await facade.prepareTrackedRecord({ deliveryId, env: context.env, fence: invokingFence });
        if (!prepared.ok) return { kind: "blocked", blockers: [...prepared.blockers] };
        return { kind: "ok", summary: `tracked record written at ${prepared.relativePath}; commit it through native git tooling` };
      }
      case "confirm-record": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const confirmed = await facade.confirmTrackedRecord({ deliveryId, fence: invokingFence });
        if (!confirmed.ok) return { kind: "blocked", blockers: [...confirmed.blockers] };
        return { kind: "ok", summary: `tracked record verified; delivery is ${confirmed.state}` };
      }
      case "finish": {
        const invokingFence = requireFence();
        if (typeof invokingFence !== "number") return invokingFence;
        const finished = await facade.completeFinishLine({ deliveryId, fence: invokingFence });
        if (!finished.ok) return { kind: "blocked", blockers: [...finished.blockers] };
        return { kind: "ok", summary: `merge-ready; delivery is ${finished.state} (result ${finished.resultDigest})` };
      }
      case "explain-blocker": {
        const explained = await facade.explainBlocker({ deliveryId });
        if (!explained.ok) return { kind: "blocked", blockers: [...explained.blockers] };
        emit(explained.blocker ?? { blocker: null });
        return { kind: "ok", summary: explained.blocker === void 0 ? "no blocker recorded" : `blocker ${explained.blocker.code}` };
      }
      default:
        return { kind: "usage", message: `Unknown managed operation: ${operation}.` };
    }
  }
};

// packages/cli/src/commands/emit-review-evidence.ts
import { readFile as readFile8 } from "node:fs/promises";
import path17 from "node:path";
var emitReviewEvidenceCommand = {
  capturesCandidate: true,
  name: "emit-review-evidence",
  sourceId: "delivery-harness.cli.emit-review-evidence",
  summary: "Bind concluded review outcomes to their original prepared context.",
  usage: "Usage: delivery-harness emit-review-evidence --context <review-context.json>\nReads a review-outcome/1 document on stdin.",
  async run(context) {
    if (context.args.length !== 2 || context.args[0] !== "--context" || !context.args[1] || context.args[1].startsWith("-")) {
      return { kind: "usage", message: "emit-review-evidence requires --context <review-context.json> and a review-outcome/1 document on stdin." };
    }
    let original;
    let document;
    try {
      original = JSON.parse(await readFile8(path17.resolve(context.rootDir, context.args[1]), "utf8"));
      const raw = await context.readStdin?.() ?? "";
      if (!raw.trim()) return { kind: "usage", message: "The review-outcome/1 document is required on stdin." };
      document = JSON.parse(raw);
    } catch {
      return { kind: "usage", message: "The original context and review outcome must be readable JSON documents." };
    }
    try {
      const result = await emitReviewEvidence(context, original, document);
      return { kind: "ok", summary: result.manifestPath };
    } catch (error) {
      if (!(error instanceof OutcomeError)) throw error;
      return { kind: "blocked", blockers: [commandBlocker({
        code: "review_outcome_invalid",
        sourceId: "delivery-harness.cli.emit-review-evidence",
        summary: error.message,
        remediations: [{ id: "supply-reviewed-context", kind: "manual_action", summary: "Use the original review-context --json output and concluded outcomes naming its digest; acquire a new review if the deliverable or review inputs changed." }]
      })] };
    }
  }
};

// packages/cli/src/run-view-record.ts
import { realpath as realpath5 } from "node:fs/promises";
import path19 from "node:path";

// packages/cli/src/run-archive-commands.ts
import { constants } from "node:fs";
import { open as open2, realpath as realpath4 } from "node:fs/promises";
import path18 from "node:path";
import {
  createArtifactsPort as createArtifactsPort3,
  readRunArtifact,
  MAX_PORTABLE_RECORD_BYTES as MAX_PORTABLE_RECORD_BYTES3
} from "./kernel.mjs";

// packages/cli/src/run-archive.ts
import {
  applySecretDiscipline as applySecretDiscipline3,
  canonicalize as canonicalize3,
  MAX_PORTABLE_RECORD_BYTES as MAX_PORTABLE_RECORD_BYTES2,
  MAX_PORTABLE_ARTIFACTS as MAX_PORTABLE_ARTIFACTS2,
  MAX_PORTABLE_EVIDENCE_BYTES as MAX_PORTABLE_EVIDENCE_BYTES2
} from "./kernel.mjs";

// packages/cli/src/run-attachments.ts
import {
  canonicalize,
  sha256Hex as sha256Hex6,
  portableArtifactContents as portableArtifactContents2,
  applySecretDiscipline,
  MAX_PORTABLE_ARTIFACTS,
  MAX_PORTABLE_EVIDENCE_BYTES
} from "./kernel.mjs";
var record = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
function referencedAttachments(events) {
  const entries = /* @__PURE__ */ new Map();
  for (const event of events) {
    if (event.kind !== "artifact.referenced") continue;
    const metadata = event.payload;
    const prior = entries.get(metadata.artifactId);
    if (prior && canonicalize(prior) !== canonicalize(metadata))
      return void 0;
    entries.set(metadata.artifactId, metadata);
  }
  return entries;
}
function validateRunAttachments(value2, events) {
  if (!record(value2) || Object.keys(value2).sort().join(",") !== "blobs,entries" || !Array.isArray(value2["entries"]) || !record(value2["blobs"]) || Buffer.byteLength(JSON.stringify(value2), "utf8") > MAX_PORTABLE_EVIDENCE_BYTES)
    return false;
  const refs = referencedAttachments(events);
  if (!refs || refs.size > MAX_PORTABLE_ARTIFACTS || value2["entries"].length !== refs.size)
    return false;
  const seen = /* @__PURE__ */ new Set();
  const needed = /* @__PURE__ */ new Set();
  const read = portableArtifactContents2(value2["blobs"]);
  if (read.blockers.length) return false;
  for (const entry of value2["entries"]) {
    if (!record(entry) || !record(entry["metadata"])) return false;
    const metadata = entry["metadata"];
    if (seen.has(metadata.artifactId) || !refs.has(metadata.artifactId) || canonicalize(metadata) !== canonicalize(refs.get(metadata.artifactId)))
      return false;
    seen.add(metadata.artifactId);
    if (entry["availability"] === "unavailable") {
      if (Object.keys(entry).sort().join(",") !== "availability,code,metadata,reason" || !["missing", "access_refused"].includes(String(entry["code"])) || typeof entry["reason"] !== "string" || !entry["reason"].length || entry["reason"].length > 512)
        return false;
    } else if (entry["availability"] === "retained") {
      if (Object.keys(entry).sort().join(",") !== "availability,metadata")
        return false;
      const observed = read.observations.get(metadata.digest);
      if (!observed || observed.base64 === void 0) return false;
      const bytes = Buffer.from(observed.base64, "base64");
      if (bytes.length !== metadata.sizeBytes || sha256Hex6(bytes) !== metadata.digest)
        return false;
      let structured = null;
      try {
        structured = JSON.parse(bytes.toString("utf8"));
      } catch {
      }
      if (!applySecretDiscipline(
        { text: bytes.toString("utf8"), structured },
        /* @__PURE__ */ new Set()
      ).ok)
        return false;
      needed.add(metadata.digest);
    } else return false;
  }
  return Object.keys(value2["blobs"]).length === needed.size && Object.keys(value2["blobs"]).every((key) => needed.has(key)) && applySecretDiscipline(value2["entries"], /* @__PURE__ */ new Set()).ok;
}

// packages/cli/src/run-export.ts
import {
  canonicalize as canonicalize2,
  MAX_PORTABLE_RECORD_BYTES,
  applySecretDiscipline as applySecretDiscipline2,
  evaluateRunJournal as evaluateRunJournal2,
  RUN_STORE_ID as RUN_STORE_ID2,
  validateRunEvent,
  runActionReconciliationError
} from "./kernel.mjs";

// packages/cli/src/run-projection.ts
import {
  RUN_JOURNAL_REQUIRED_ENTRIES,
  explainRunJournal as explainRunJournal2,
  runJournalCarries,
  runPrimaryTicket,
  projectRunActivities
} from "./kernel.mjs";
var READOUT_LABELS = "self-attested; observability, not evidence; unbound to a record";
function projectRunProgress(events, now, freshnessWindowMs) {
  const projection = projectRunActivities(events, { now, ...freshnessWindowMs === void 0 ? {} : { freshnessWindowMs } });
  const latestFindings = /* @__PURE__ */ new Map();
  for (const finding of projection.findings) {
    if (finding.current) latestFindings.set(String(finding.payload["findingId"]), finding);
  }
  return {
    asOf: now,
    ...projection,
    currentFindings: [...latestFindings.values()],
    findingsCoverage: events.some((e) => e.kind === "finding.observed") ? "reported" : "unreported"
  };
}
function costLabel(value2) {
  const cost = typeof value2 === "object" && value2 !== null ? value2 : {};
  if (cost["coverage"] === "unreported" || cost["total"] === void 0) return "unreported";
  const measured = `${oneLineOf(cost["total"])} ${oneLineOf(cost["unit"])}`.trim();
  return cost["coverage"] === "partial" ? `${measured} (partial coverage)` : measured;
}
function projectCosts(events) {
  const allClosed = events.filter((event) => event.kind === "review.round.closed");
  const snapshots = /* @__PURE__ */ new Map();
  for (const event of allClosed) {
    const payload = payloadOf(event);
    const cost = payload["cost"];
    const key = event.version === "run-event/2" ? JSON.stringify([payload["roundId"], cost["reportedBy"], cost["unit"] ?? "unreported"]) : `legacy-${event.seq}`;
    snapshots.set(key, event);
  }
  const closed = [...snapshots.values()];
  const totals = /* @__PURE__ */ new Map();
  let unreportedEntries = 0;
  let partial = allClosed.length !== closed.length || events.filter((event) => event.kind === "review.round.opened").length !== closed.length;
  for (const event of closed) {
    const cost = payloadOf(event)["cost"];
    if (cost["coverage"] === "unreported") {
      unreportedEntries += 1;
      continue;
    }
    if (cost["coverage"] === "partial") partial = true;
    const unit = cost["unit"];
    const reportedBy = cost["reportedBy"];
    const key = JSON.stringify([reportedBy, unit]);
    const prior = totals.get(key);
    const sum = prior?.total === null ? Infinity : (prior?.total ?? 0) + cost["total"];
    if (!Number.isFinite(sum)) partial = true;
    totals.set(key, { unit, total: Number.isFinite(sum) ? sum : null, reportedBy });
  }
  const ended = events.find((event) => event.kind === "run.ended");
  return {
    review: {
      coverage: totals.size === 0 ? "unreported" : partial || unreportedEntries > 0 ? "partial" : "complete",
      unreportedEntries,
      totals: [...totals.values()]
    },
    run: ended === void 0 ? { coverage: "unreported" } : payloadOf(ended)["cost"]
  };
}
var payloadOf = (event) => typeof event.payload === "object" && event.payload !== null ? event.payload : {};
var boundTicket = (event) => event.ticket === void 0 ? "" : ` for ${oneLineOf(event.ticket, 128)}`;
function detailOf(event) {
  const payload = payloadOf(event);
  switch (event.kind) {
    case "run.started":
      return oneLineOf(payload["host"]) + (payload["displacedRunId"] === void 0 ? "" : ` displaced ${oneLineOf(payload["displacedRunId"])}`);
    case "run.ended":
      return `${oneLineOf(payload["result"])} cost ${costLabel(payload["cost"])}`;
    case "ticket.read":
      return `${oneLineOf(payload["ticket"])} via ${oneLineOf(payload["tracker"])}`;
    case "posture.declared":
      return oneLineOf(payload["posture"]) + boundTicket(event);
    case "lens.selected":
      return `mandated ${oneLineOf(payload["mandated"])} selected ${oneLineOf(payload["selected"])} \u2014 ${oneLineOf(payload["rationale"])}`;
    case "review.round.opened":
      return `round ${oneLineOf(payload["round"])} on ${oneLineOf(event.candidateTreeSha)} lenses ${oneLineOf(payload["lenses"])}`;
    case "review.round.closed":
      return `round ${oneLineOf(payload["round"])} ${oneLineOf(payload["outcome"])} findings ${oneLineOf(payload["findings"])}`;
    case "command.completed":
      return `${oneLineOf(payload["command"])} ${oneLineOf(payload["outcome"])} in ${oneLineOf(payload["durationMs"])}ms`;
    case "gate.reported":
      return `${oneLineOf(payload["command"])} ${oneLineOf(payload["outcome"])} in ${oneLineOf(payload["durationMs"])}ms${boundTicket(event)}`;
    case "pr.opened":
      return `${oneLineOf(payload["url"], 400)} on ${oneLineOf(event.candidateTreeSha)}${boundTicket(event)}`;
    case "blocker.recorded":
      return `${oneLineOf(payload["code"])} \u2014 ${oneLineOf(payload["summary"])}`;
    case "decision.recorded":
      return `${oneLineOf(payload["fork"])} \u2014 ${oneLineOf(payload["choice"])}${payload["cited"] === void 0 ? "" : ` (cited ${oneLineOf(payload["cited"])})`}`;
    case "compounding.recorded":
      return `${oneLineOf(payload["outcome"])}${payload["reference"] === void 0 ? "" : ` \u2014 ${oneLineOf(payload["reference"])}`}`;
    case "context.saved":
      return `stage ${oneLineOf(payload["stage"])} (observation only)`;
    case "action.reconciled":
      return `${oneLineOf(payload["actionId"])} reconciled ${oneLineOf(payload["outcome"])} (host-attested) evidence ${oneLineOf(payload["evidenceReference"])}`;
    case "action.intent":
    case "action.observed":
      return `${oneLineOf(payload["actionId"])} ${oneLineOf(payload["outcome"] ?? "unknown")} reference ${oneLineOf(payload["reference"])}`;
    default:
      return "";
  }
}
function roundEntries(events) {
  const rounds = /* @__PURE__ */ new Map();
  for (const event of events) {
    if (event.kind !== "review.round.opened" && event.kind !== "review.round.closed") continue;
    const key = oneLineOf(payloadOf(event)["round"], 32);
    const entry = rounds.get(key) ?? {};
    if (event.kind === "review.round.opened") entry.opened = event;
    else entry.closed = event;
    rounds.set(key, entry);
  }
  return [...rounds.entries()].map(([round, entry]) => {
    const anchor = entry.opened ?? entry.closed;
    return {
      round,
      candidateTreeSha: oneLineOf(anchor?.candidateTreeSha),
      ...entry.opened === void 0 ? {} : { opened: entry.opened },
      ...entry.closed === void 0 ? {} : { closed: entry.closed }
    };
  });
}
function roundLenses(entry) {
  return entry.opened === void 0 ? "never opened" : oneLineOf(payloadOf(entry.opened)["lenses"]);
}
function roundRows(events) {
  return roundEntries(events).map((entry) => {
    const closed = entry.closed === void 0 ? void 0 : payloadOf(entry.closed);
    return [
      `  round ${entry.round}`,
      `candidate ${entry.candidateTreeSha || "(none)"}`,
      entry.opened === void 0 ? roundLenses(entry) : `lenses ${roundLenses(entry)}`,
      closed === void 0 ? "open" : `${oneLineOf(closed["outcome"])} findings ${oneLineOf(closed["findings"])} cost ${costLabel(closed["cost"])}`
    ].join("  ");
  });
}
function durationLabel(seconds) {
  const whole = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0;
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor(whole % 3600 / 60);
  const rest = whole % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  return `${rest}s`;
}
var ROUND_KINDS = /* @__PURE__ */ new Set(["review.round.opened", "review.round.closed"]);
function heldWithin(value2, low, high) {
  const at = value2 === void 0 ? Number.NaN : Date.parse(value2);
  if (!Number.isFinite(at)) return high;
  const lowAt = Date.parse(low);
  const highAt = Date.parse(high);
  if (!Number.isFinite(lowAt) || !Number.isFinite(highAt)) return high;
  if (at < lowAt) return low;
  if (at > highAt) return high;
  return value2;
}
var gateDurationOf = (event) => {
  const value2 = payloadOf(event)["durationMs"];
  return typeof value2 === "number" && Number.isFinite(value2) && value2 >= 0 ? value2 : void 0;
};
function projectGateTime(events) {
  const gates = events.filter(
    (event) => event.kind === "gate.reported" || event.kind === "command.completed" && event.actor.role === "cli" && payloadOf(event)["command"] === "gate"
  );
  let totalMs = 0;
  let unreadable = 0;
  for (const gate of gates) {
    const duration = gateDurationOf(gate);
    if (duration === void 0) unreadable += 1;
    else totalMs += duration;
  }
  return { totalMs, counted: gates.length, unreadable, unseen: gates.length === 0 };
}
function projectRunPhases(events) {
  const startedAt = events[0]?.at ?? "";
  const lastAt = events[events.length - 1]?.at ?? "";
  const reviewStart = heldWithin(events.find((event) => ROUND_KINDS.has(event.kind))?.at, startedAt, lastAt);
  const reviewEnd = heldWithin(events.findLast((event) => event.kind === "review.round.closed")?.at, reviewStart, lastAt);
  return {
    implementationSeconds: spanSeconds(startedAt, reviewStart),
    reviewSeconds: spanSeconds(reviewStart, reviewEnd),
    tailSeconds: spanSeconds(reviewEnd, lastAt),
    rounds: roundEntries(events).length,
    gate: projectGateTime(events)
  };
}
function phaseRows(events) {
  const summary = summarize(events);
  const phases2 = summary.phases;
  const gate = phases2.gate;
  const gateRow = gate.unseen ? "unseen \u2014 no gate completion is journaled, so any gate this delivery ran is missing from the tail above" : `${durationLabel(gate.totalMs / 1e3)} summed over ${gate.counted} journaled gate completion(s)` + (gate.unreadable === 0 ? "" : `; ${gate.unreadable} journaled no readable duration, so the sum under-reports`);
  return [
    "  phases:",
    `    implementation  ${durationLabel(phases2.implementationSeconds)}  (first event to the first round)`,
    `    review          ${durationLabel(phases2.reviewSeconds)}  over ${phases2.rounds} round(s), fix time between rounds included`,
    `    tail            ${durationLabel(phases2.tailSeconds)}  (last closed round to the last event)`,
    `    gate time       ${gateRow}`,
    `    total           ${durationLabel(summary.durationSeconds)}${summary.open ? "  (open; the tail is still accruing)" : ""}`
  ];
}
function readoutOf(events, evaluation, rootDir) {
  const present = RUN_JOURNAL_REQUIRED_ENTRIES.filter((entry) => runJournalCarries(events, entry));
  const note = evaluation.status === "complete-executor-only" && rootDir !== void 0 && harnessConfigPresentAt(rootDir) ? `no CLI gate completion in this journal; harness.config.ts present at ${oneLine(rootDir, 400)}` : void 0;
  const explanations = evaluation.violations.length === 0 ? void 0 : explainRunJournal2(events).explanations;
  return {
    status: evaluation.status,
    present,
    missing: evaluation.missing,
    violations: evaluation.violations,
    ...explanations === void 0 ? {} : { explanations },
    ...note === void 0 ? {} : { note }
  };
}
function readoutRows(events, evaluation, rootDir) {
  const readout = readoutOf(events, evaluation, rootDir);
  const rows = [
    `  completeness: ${readout.status}  (${READOUT_LABELS})`,
    `    present: ${readout.present.length === 0 ? "(none)" : readout.present.join(", ")}`,
    `    missing: ${readout.missing.length === 0 ? "(none)" : [...readout.missing].join(", ")}`
  ];
  if (readout.violations.length > 0) {
    rows.push(`    violations: ${[...readout.violations].join(", ")}`);
    for (const explanation of readout.explanations ?? []) {
      rows.push(`      ${explanation.violation}: ${oneLine(explanation.because, 400)}`);
    }
    rows.push("    admission: none of the above blocks admission; no gate, admission, or record decision reads a journal.");
  }
  if (readout.note !== void 0) rows.push(`    note: ${readout.note}`);
  return rows;
}
var cliCompletionFor = (events, command) => events.findLast(
  (event) => event.kind === "command.completed" && event.actor.role === "cli" && payloadOf(event)["command"] === command
);
var severityOf = (value2, key) => {
  const findings = typeof value2 === "object" && value2 !== null ? value2 : {};
  const count = findings[key];
  return typeof count === "number" && Number.isFinite(count) ? count : 0;
};
function spanSeconds(from, to) {
  const start = Date.parse(from);
  const end = Date.parse(to);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 0;
  return Math.round((end - start) / 1e3);
}
function summarize(events) {
  const first = events[0];
  const last = events[events.length - 1];
  const startedAt = first?.at ?? "";
  const lastAt = last?.at ?? "";
  const ended = events.find((event) => event.kind === "run.ended");
  const gateCompletion = cliCompletionFor(events, "gate");
  const gateReported = events.find((event) => event.kind === "gate.reported");
  const recordCompletion = cliCompletionFor(events, "record");
  const closed = events.filter((event) => event.kind === "review.round.closed");
  const gate = gateCompletion !== void 0 ? { outcome: oneLineOf(payloadOf(gateCompletion)["outcome"], 64), writer: "cli" } : gateReported !== void 0 ? { outcome: oneLineOf(payloadOf(gateReported)["outcome"], 64), writer: "executor" } : void 0;
  return {
    ticket: oneLineOf(runPrimaryTicket(events), 128),
    open: ended === void 0,
    startedAt,
    lastAt,
    durationSeconds: spanSeconds(startedAt, lastAt),
    phases: projectRunPhases(events),
    roundsOpened: events.filter((event) => event.kind === "review.round.opened").length,
    roundsClosed: closed.length,
    findings: {
      P0: closed.reduce((total, event) => total + severityOf(payloadOf(event)["findings"], "P0"), 0),
      P1: closed.reduce((total, event) => total + severityOf(payloadOf(event)["findings"], "P1"), 0),
      P2: closed.reduce((total, event) => total + severityOf(payloadOf(event)["findings"], "P2"), 0),
      P3: closed.reduce((total, event) => total + severityOf(payloadOf(event)["findings"], "P3"), 0)
    },
    ...gate === void 0 ? {} : { gate },
    ...recordCompletion === void 0 ? {} : { record: { outcome: oneLineOf(payloadOf(recordCompletion)["outcome"], 64), writer: "cli" } },
    ...ended === void 0 ? {} : { result: oneLineOf(payloadOf(ended)["result"], 64) }
  };
}

// packages/cli/src/run-export.ts
function buildRunExport(input) {
  const v2 = input.events[0]?.version === "run-event/2";
  return {
    spec: v2 ? "delivery-run-export/2" : "delivery-run-export/1",
    labels: READOUT_LABELS,
    runId: input.runId,
    events: input.events,
    summary: summarize(input.events),
    costs: projectCosts(input.events),
    readout: readoutOf(
      input.events,
      evaluateRunJournal2(input.events),
      input.rootDir
    ),
    refusedAppends: input.refusedAppends ?? [],
    // Exported progress is a historical projection at the last observation,
    // never a claim about whether the producer is executing now.
    ...v2 ? {
      progress: projectRunProgress(
        input.events,
        input.events.at(-1)?.at ?? ""
      )
    } : {}
  };
}
function comparableReadout(readout) {
  const comparable = { ...readout };
  delete comparable["explanations"];
  return comparable;
}
function comparableSummary(summary) {
  const comparable = { ...summary };
  delete comparable["phases"];
  return comparable;
}
var isRecord2 = (value2) => value2 !== null && typeof value2 === "object" && !Array.isArray(value2);
function parseRunExport(text2) {
  const invalid = { ok: false, code: "run_export_invalid" };
  try {
    if (Buffer.byteLength(text2, "utf8") > MAX_PORTABLE_RECORD_BYTES)
      return invalid;
    const value2 = JSON.parse(text2);
    if (!isRecord2(value2) || !["delivery-run-export/1", "delivery-run-export/2"].includes(
      String(value2["spec"])
    ) || value2["labels"] !== READOUT_LABELS || typeof value2["runId"] !== "string" || value2["runId"].length > 128 || !RUN_STORE_ID2.test(value2["runId"]) || !Array.isArray(value2["events"]) || !Array.isArray(value2["refusedAppends"]) || !isRecord2(value2["readout"]))
      return invalid;
    const v2 = value2["spec"] === "delivery-run-export/2";
    const hasAttachments = Object.prototype.hasOwnProperty.call(
      value2,
      "attachments"
    );
    if (hasAttachments && (!v2 || !applySecretDiscipline2(value2, /* @__PURE__ */ new Set()).ok))
      return invalid;
    if (Object.keys(value2).filter((key) => key !== "attachments").sort().join(",") !== (v2 ? "costs,events,labels,progress,readout,refusedAppends,runId,spec,summary" : "costs,events,labels,readout,refusedAppends,runId,spec,summary"))
      return invalid;
    const prefix = [];
    for (const [index, event] of value2["events"].entries()) {
      if (!validateRunEvent(event).ok || !isRecord2(event) || event["runId"] !== value2["runId"] || event["seq"] !== index + 1 || event["version"] !== (v2 ? "run-event/2" : "run-event/1"))
        return invalid;
      const validated = event;
      if (runActionReconciliationError(prefix, validated)) return invalid;
      prefix.push(validated);
    }
    if (hasAttachments && !validateRunAttachments(
      value2["attachments"],
      value2["events"]
    ))
      return invalid;
    const expected = buildRunExport({
      runId: value2["runId"],
      events: value2["events"]
    });
    if (expected.spec !== value2["spec"] || canonicalize2(value2["progress"] ?? null) !== canonicalize2(expected.progress ?? null))
      return invalid;
    if (!isRecord2(value2["summary"]) || canonicalize2(comparableSummary(value2["summary"])) !== canonicalize2(comparableSummary(expected.summary)) || canonicalize2(value2["costs"]) !== canonicalize2(expected.costs))
      return invalid;
    const { note, ...readout } = value2["readout"];
    if (note !== void 0 && typeof note !== "string" || canonicalize2(comparableReadout(readout)) !== canonicalize2(comparableReadout(expected.readout)))
      return invalid;
    return {
      ok: true,
      value: {
        ...expected,
        ...hasAttachments ? { attachments: value2["attachments"] } : {},
        refusedAppends: value2["refusedAppends"],
        readout: {
          ...expected.readout,
          ...note === void 0 ? {} : { note }
        }
      }
    };
  } catch {
    return invalid;
  }
}

// packages/cli/src/run-archive.ts
async function buildRunArchive(input) {
  const base = buildRunExport(input);
  if (Buffer.byteLength(JSON.stringify(base), "utf8") > MAX_PORTABLE_RECORD_BYTES2)
    return { ok: false, reason: "archive exceeds 16 MiB serialized limit" };
  if (!parseRunExport(JSON.stringify(base)).ok)
    return { ok: false, reason: "invalid run export" };
  if (!applySecretDiscipline3(base, /* @__PURE__ */ new Set()).ok)
    return { ok: false, reason: "archive contains a secret-like value" };
  const refs = referencedAttachments(input.events);
  if (refs && refs.size > MAX_PORTABLE_ARTIFACTS2)
    return { ok: false, reason: "archive exceeds 128 attachments" };
  if (!refs)
    return { ok: false, reason: "conflicting attachment reference bindings" };
  const entries = [];
  const blobs = /* @__PURE__ */ Object.create(null);
  if (base.spec === "delivery-run-export/2")
    for (const metadata of refs.values()) {
      const artifact = await input.readArtifact(metadata.artifactId);
      if (!artifact.ok) {
        if (artifact.code !== "missing" && artifact.code !== "access_refused")
          return {
            ok: false,
            reason: `retained attachment ${artifact.code}; export refused`
          };
        entries.push({
          metadata,
          availability: "unavailable",
          code: artifact.code,
          reason: artifact.reason.slice(0, 512)
        });
        continue;
      }
      if (canonicalize3(artifact.metadata) !== canonicalize3(metadata))
        return { ok: false, reason: "attachment binding differs from journal" };
      if (blobs[metadata.digest] !== void 0 && blobs[metadata.digest] !== artifact.base64)
        return { ok: false, reason: "conflicting attachment bytes" };
      blobs[metadata.digest] = artifact.base64;
      entries.push({ metadata, availability: "retained" });
      if (Buffer.byteLength(JSON.stringify({ entries, blobs }), "utf8") > MAX_PORTABLE_EVIDENCE_BYTES2)
        return {
          ok: false,
          reason: "archive exceeds 8 MiB serialized attachment limit"
        };
    }
  const attachments = { entries, blobs };
  if (base.spec === "delivery-run-export/2" && !validateRunAttachments(attachments, input.events))
    return {
      ok: false,
      reason: "attachment bytes, bindings or serialized limits are invalid"
    };
  const text2 = JSON.stringify({
    ...base,
    ...base.spec === "delivery-run-export/2" ? { attachments } : {}
  });
  if (Buffer.byteLength(text2, "utf8") > MAX_PORTABLE_RECORD_BYTES2)
    return { ok: false, reason: "archive exceeds 16 MiB serialized limit" };
  return { ok: true, text: text2 };
}
function readArchiveArtifact(text2, artifactId) {
  if (Buffer.byteLength(text2, "utf8") > MAX_PORTABLE_RECORD_BYTES2)
    return {
      ok: false,
      code: "invalid",
      reason: "archive exceeds 16 MiB serialized limit"
    };
  const parsed = parseRunExport(text2);
  if (!parsed.ok)
    return { ok: false, code: "invalid", reason: "archive invalid" };
  const entry = parsed.value.attachments?.entries.find(
    (e) => e.metadata.artifactId === artifactId
  );
  if (!entry)
    return {
      ok: false,
      code: "invalid",
      reason: "attachment not retained in this archive"
    };
  if (entry.availability === "unavailable")
    return { ok: false, code: entry.code, reason: entry.reason };
  return {
    ok: true,
    metadata: entry.metadata,
    base64: parsed.value.attachments.blobs[entry.metadata.digest]
  };
}

// packages/cli/src/run-archive-commands.ts
var blocked3 = (reason) => ({
  kind: "blocked",
  blockers: [
    runSurfaceBlocker({
      code: "run_archive_unavailable",
      summary: "The run archive is unavailable.",
      details: oneLine(reason),
      remediation: {
        id: "inspect-run-archive",
        summary: "Check the selected archive or retained run attachments and retry."
      }
    })
  ]
});
async function readArchiveFile(file) {
  const h = await open2(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat4 = await h.stat();
    if (!stat4.isFile() || stat4.size > MAX_PORTABLE_RECORD_BYTES3)
      throw Error("archive must be a regular file no larger than 16 MiB");
    const buffer = Buffer.alloc(
      Math.min(stat4.size + 1, MAX_PORTABLE_RECORD_BYTES3 + 1)
    );
    let offset = 0;
    while (offset < buffer.length) {
      const read = await h.read(buffer, offset, buffer.length - offset, null);
      if (!read.bytesRead) break;
      offset += read.bytesRead;
    }
    if (offset !== stat4.size || offset > MAX_PORTABLE_RECORD_BYTES3)
      throw Error("archive changed size while reading");
    return buffer.subarray(0, offset).toString("utf8");
  } finally {
    await h.close();
  }
}
async function resolvedDestination(file) {
  let parent = path18.dirname(file);
  const suffix = [path18.basename(file)];
  for (; ; ) {
    try {
      return path18.join(await realpath4(parent), ...suffix);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      suffix.unshift(path18.basename(parent));
      const next = path18.dirname(parent);
      if (next === parent) throw error;
      parent = next;
    }
  }
}
var within = (parent, child) => {
  const relative = path18.relative(parent, child);
  return relative === "" || !relative.startsWith("..") && !path18.isAbsolute(relative);
};
async function runArchiveCommand(context, command, args) {
  if (command === "export" && (args.length !== 3 || args[1] !== "--output"))
    return {
      kind: "usage",
      message: "Usage: runs export <run-id> --output <file>"
    };
  if (command === "archive" && !(args.length === 1 || args.length === 3 && args[1] === "--artifact"))
    return {
      kind: "usage",
      message: "Usage: runs archive <file> [--artifact <id>]"
    };
  try {
    if (command === "archive") {
      const text2 = await readArchiveFile(
        path18.resolve(context.rootDir, args[0])
      );
      const parsed = parseRunExport(text2);
      if (!parsed.ok) return blocked3("archive invalid or unsupported");
      if (args[1] === "--artifact") {
        const artifact = readArchiveArtifact(text2, args[2]);
        if (!artifact.ok) return blocked3(artifact.reason);
        context.write(
          JSON.stringify({
            spec: "run-artifact/1",
            historical: true,
            authority: "observation",
            runId: parsed.value.runId,
            ...artifact
          })
        );
      } else
        context.write(
          JSON.stringify({
            historical: true,
            authority: "observation",
            archive: parsed.value
          })
        );
      return { kind: "ok" };
    }
    const resolved = await resolveRunSurface(context.rootDir);
    if (!resolved.ok) return blocked3(resolved.reason);
    const store = resolved.surface.store;
    const runId = args[0];
    const journal = await store.read(runId);
    if (!journal.ok) return blocked3("run unavailable");
    const archive = await buildRunArchive({
      runId,
      events: journal.events,
      refusedAppends: await store.readNotes(runId),
      readArtifact: (id) => readRunArtifact(store, runId, id)
    });
    if (!archive.ok) return blocked3(archive.reason);
    const destination = await resolvedDestination(
      path18.resolve(context.rootDir, args[2])
    );
    if (within(await realpath4(resolved.surface.commonDir), destination) || within(await realpath4(resolved.surface.runsDir), destination))
      return blocked3(
        "archive output cannot replace repository metadata or the live run store"
      );
    await createArtifactsPort3().writeTextFile(destination, archive.text, {
      mode: 384
    });
    context.write(
      JSON.stringify({
        spec: "run-archive-export/1",
        runId,
        path: path18.resolve(context.rootDir, args[2]),
        sizeBytes: Buffer.byteLength(archive.text),
        authority: "observation"
      })
    );
    return { kind: "ok" };
  } catch {
    return blocked3("archive file unreadable, oversized, unsafe, or unwritable");
  }
}

// packages/cli/src/run-view-record.ts
import {
  isSafeRelativePath,
  isInsideResolved,
  parseDeliveryRecord,
  digestCanonical as digestCanonical8
} from "./kernel.mjs";
async function withRetainedRecord(view, root, relativePath) {
  if (relativePath === void 0 || view.historical) return view;
  const fields = [];
  const add = (label2, value2) => fields.push({ label: label2, value: value2 });
  add("Source", relativePath);
  add(
    "Applicability",
    "Unknown \u2014 no current identity comparison or fresh verification is performed"
  );
  add(
    "Original verification time",
    "Unavailable \u2014 delivery records do not store a verification timestamp"
  );
  try {
    let status = "path_refused";
    let contents = null;
    if (isSafeRelativePath(relativePath)) {
      const resolvedRoot = await realpath5(root);
      const resolvedFile = await realpath5(
        path19.join(resolvedRoot, relativePath)
      );
      if (!isInsideResolved(resolvedRoot, resolvedFile))
        status = "outside_run_root";
      else {
        contents = await readArchiveFile(resolvedFile);
        status = "readable";
      }
    }
    const read = { status, contents };
    if (read.status !== "readable" || read.contents === null) {
      add("Status", `Unavailable \u2014 ${read.status}`);
    } else {
      const parsed = parseDeliveryRecord(read.contents);
      if (!parsed.ok)
        add("Status", "Corrupt \u2014 retained record grammar is invalid");
      else {
        const { integrityDigest, ...unsigned } = parsed.record;
        if (parsed.record.version !== "delivery-record/2")
          add(
            "Status",
            "Legacy record \u2014 integrity unavailable; no accepted evidence inferred"
          );
        else if (integrityDigest !== digestCanonical8(unsigned))
          add("Status", "Corrupt \u2014 retained record digest does not match");
        else {
          add(
            "Status",
            "Recorded for the candidate below \u2014 retained self-attested record, not current approval"
          );
          add("Candidate", parsed.record.candidateBinding.treeSha);
          add(
            "Deliverable digest",
            parsed.record.candidateBinding.deliverableDigest
          );
          for (const claim of parsed.record.claims) {
            add(`Recorded claim \xB7 ${claim.obligationId}`, claim.outcome);
            for (const evidence of [
              claim.evidence,
              ...claim.supportingEvidence ?? []
            ]) {
              if (evidence?.resolution.kind !== "evidence") continue;
              const manifest = evidence.resolution.portable?.manifest;
              if (typeof manifest !== "object" || manifest === null) continue;
              if (digestCanonical8(manifest) !== evidence.resolution.manifestDigest) {
                add(
                  "Retained manifest",
                  "Corrupt \u2014 digest does not match; timestamp unavailable"
                );
                continue;
              }
              const at = manifest["recordedAt"];
              add(
                `Provider-reported recording time \xB7 ${evidence.resolution.providerId}`,
                typeof at === "string" ? at : "Unavailable"
              );
            }
          }
        }
      }
    }
  } catch (error) {
    add(
      "Status",
      error.code === "ENOENT" ? "Unavailable \u2014 missing" : "Unavailable \u2014 bounded retained record read refused"
    );
  }
  const retained = {
    id: "retained-record",
    label: "Retained delivery record",
    fields
  };
  return {
    ...view,
    sections: view.sections.map(
      (section) => section.id === "evidence" ? { ...section, items: [retained, ...section.items] } : section
    )
  };
}

// packages/cli/src/run-view.ts
var value = (v) => v === void 0 ? "Unreported" : typeof v === "string" ? v : JSON.stringify(v);
var item = (id, label2, fields, artifactId) => ({
  id,
  label: label2,
  fields: Object.entries(fields).map(([label3, v]) => ({
    label: label3,
    value: value(v)
  })),
  ...artifactId === void 0 ? {} : { artifactId }
});
function projectRunView(events, options) {
  const p = projectRunProgress(events, options.now, options.freshnessWindowMs);
  const historical = options.historical === true;
  const costs = projectCosts(events);
  const runCost = costs.run;
  const attempts = p.activities.flatMap((a) => a.attempts);
  const current = attempts.filter((a) => !a.superseded);
  const sections = [
    {
      id: "waiting",
      title: "Waiting and required action",
      empty: "No current waiting observation. Unreported waits remain unknown.",
      items: p.waits.filter((w) => w.current && !w.resolvedAt).map(
        (w) => item(w.waitId, w.reason, {
          Owner: w.owner,
          "Human action required": w.waitingOn === "human" ? "Yes \u2014 human" : w.waitingOn === "unknown" ? "Unknown" : `No \u2014 ${w.waitingOn}`,
          "Next action": w.nextAction,
          Scope: w.scope,
          Attempt: w.attemptId,
          Observed: w.startedAt,
          Freshness: historical ? "Historical observation" : !Number.isFinite(Date.parse(w.startedAt)) ? "unknown" : Date.parse(options.now) - Date.parse(w.startedAt) > (options.freshnessWindowMs ?? 3e5) ? "stale" : "recent",
          Candidate: w.candidateTreeSha
        })
      )
    },
    {
      id: "work",
      title: "Current work",
      empty: attempts.length === 0 ? "No activity observations; execution status unknown." : "No active work in the latest observations. Retained attempts appear in history.",
      items: current.filter(
        (a) => !["completed", "failed", "interrupted"].includes(a.state)
      ).map(
        (a) => item(a.attemptId, a.activityId, {
          Owner: a.owner,
          Phase: a.phase,
          State: `${a.state} (reported)`,
          Freshness: historical ? "Historical observation" : a.freshness,
          "Last observed": a.lastObservedAt,
          "Elapsed since start": a.startedAt ? `${Math.max(0, Math.floor((Date.parse(options.now) - Date.parse(a.startedAt)) / 1e3))}s` : "Unknown \u2014 start not observed",
          "Next step": a.nextStep,
          Candidate: a.candidateTreeSha,
          "Lifecycle history": a.lifecycleIncomplete ? "Incomplete" : "Observed"
        })
      )
    },
    {
      id: "reviews",
      title: "Reviewer attempts and history",
      empty: "No reviewer attempt observations.",
      items: attempts.filter((a) => a.lensId !== void 0 || a.phase === "review").map(
        (a) => item(a.attemptId, a.lensId ?? a.activityId, {
          Attempt: a.attemptId,
          Round: a.round,
          "Round ID": a.roundId,
          Owner: a.owner,
          State: a.state,
          History: a.superseded ? "Superseded attempt" : "Latest observed attempt",
          Verdict: a.verdict,
          Cost: costLabel(a.cost),
          Candidate: a.candidateTreeSha
        })
      )
    },
    {
      id: "findings",
      title: "Current unresolved findings",
      empty: p.findingsCoverage === "unreported" ? "Unknown \u2014 detailed finding observations were not recorded." : "No unresolved findings in latest reported observations; this is not approval.",
      items: p.currentFindings.filter((f) => f.payload["state"] === "unresolved").map(
        (f) => item(String(f.payload["findingId"]), String(f.payload["findingId"]), {
          Severity: f.payload["severity"],
          State: "Unresolved (reported)",
          Report: f.payload["reportId"],
          Attempt: f.payload["attemptId"],
          Candidate: f.payload["candidateTreeSha"]
        })
      )
    },
    {
      id: "evidence",
      title: "Evidence and candidate",
      empty: "No evidence observations. Applicability unknown.",
      items: events.filter(
        (e) => [
          "gate.reported",
          "command.completed",
          "review.round.closed"
        ].includes(e.kind)
      ).map(
        (e) => item(String(e.seq), e.kind, {
          "Reported at": e.at,
          Candidate: e.candidateTreeSha ?? e.payload["candidateTreeSha"],
          Observation: e.payload,
          Authority: "Reported only \u2014 Applicability unknown; no verification is run by this view"
        })
      )
    },
    {
      id: "reports",
      title: "Retained reports",
      empty: "No report references recorded.",
      items: p.reports.map(
        (r) => item(
          String(r.payload["reportId"]),
          String(r.payload["role"]),
          {
            Report: r.payload["reportId"],
            Availability: r.payload["availability"],
            Reason: r.payload["reason"],
            "Originating report": r.payload["originatingReportId"],
            Lens: r.payload["lensId"],
            Attempt: r.payload["attemptId"],
            Candidate: r.payload["candidateTreeSha"],
            History: r.current ? "Latest observed attempt" : "Historical or incomplete binding"
          },
          typeof r.payload["artifactId"] === "string" ? r.payload["artifactId"] : void 0
        )
      )
    },
    {
      id: "finish",
      title: "Declared finish line",
      empty: "Finish steps unreported; delivery completion unknown.",
      items: []
    },
    {
      id: "cost",
      title: "Reported cost",
      empty: "Cost unreported.",
      items: [
        {
          id: "review-cost",
          label: "Review totals",
          fields: [
            ...item("review-cost", "Review totals", {
              Coverage: costs.review.coverage,
              "Unreported round entries": costs.review.unreportedEntries,
              ...costs.review.totals.length === 0 ? { Measurement: "Unreported" } : {}
            }).fields,
            ...costs.review.totals.map((total) => ({
              label: `${total.reportedBy} \xB7 ${total.unit}`,
              value: total.total === null ? "Unavailable \u2014 reported sum exceeds numeric range" : `${total.total} ${total.unit}`
            }))
          ]
        },
        item("run-cost", "Run total", {
          Coverage: runCost["coverage"],
          Measurement: costLabel(runCost),
          "Reported by": runCost["reportedBy"],
          Accounting: "Run totals can include review and attempt costs. These totals are shown separately and are not added together."
        }),
        ...attempts.filter((a) => a.cost !== void 0).map((a) => {
          const cost = a.cost;
          return item(`attempt-cost-${a.attemptId}`, `Attempt cost \xB7 ${a.activityId}`, {
            Attempt: a.attemptId,
            Owner: a.owner,
            Phase: a.phase,
            State: `${a.state} (reported)`,
            History: a.superseded ? "Superseded attempt" : "Latest observed attempt",
            Candidate: a.candidateTreeSha,
            Coverage: cost["coverage"],
            Measurement: costLabel(a.cost),
            "Reported by": cost["reportedBy"],
            Accounting: "Reported attempt measurement; not added to run or review totals, which may overlap."
          });
        })
      ]
    },
    {
      id: "activity-history",
      title: "Activity history",
      empty: "No completed, failed, interrupted or superseded activity observations.",
      items: attempts.filter((a) => a.superseded || ["completed", "failed", "interrupted"].includes(a.state)).map((a) => item(a.attemptId, a.activityId, {
        Attempt: a.attemptId,
        Owner: a.owner,
        Phase: a.phase,
        State: `${a.state} (reported)`,
        History: a.superseded ? "Superseded attempt" : "Latest observed attempt",
        Freshness: historical ? "Historical observation" : a.freshness,
        "Last observed": a.lastObservedAt,
        Candidate: a.candidateTreeSha,
        "Lifecycle history": a.lifecycleIncomplete ? "Incomplete" : "Observed",
        Cost: costLabel(a.cost)
      }))
    },
    {
      id: "finding-history",
      title: "Finding history",
      empty: "No detailed finding history.",
      items: p.findings.map(
        (f) => item(String(f.seq), String(f.payload["findingId"]), {
          State: f.payload["state"],
          Severity: f.payload["severity"],
          Attempt: f.payload["attemptId"],
          Candidate: f.payload["candidateTreeSha"],
          "Deferred issue": f.payload["deferredIssueId"],
          Observed: f.at
        })
      )
    }
  ];
  const reviewSection = sections.find((s) => s.id === "reviews");
  sections[2] = {
    ...reviewSection,
    items: [
      ...events.filter((e) => e.kind === "review.round.opened").map(
        (e) => item(`round-${e.seq}`, "Declared review round", {
          Round: e.payload["round"],
          "Round ID": e.payload["roundId"],
          Bound: e.payload["bound"],
          Grace: e.payload["grace"],
          Reopens: e.payload["reopensRoundId"],
          Candidate: e.payload["candidateTreeSha"]
        })
      ),
      ...reviewSection.items
    ]
  };
  const finish = /* @__PURE__ */ new Map();
  for (const step of p.finishSteps)
    finish.set(String(step.payload["stepId"]), step);
  sections[6] = {
    id: "finish",
    title: "Declared finish line",
    empty: "Finish steps unreported; delivery completion unknown.",
    items: [...finish.values()].map(
      (s) => item(String(s.payload["stepId"]), String(s.payload["name"]), {
        State: s.payload["state"],
        Owner: s.payload["owner"],
        Reason: s.payload["reason"],
        Candidate: s.payload["candidateTreeSha"]
      })
    )
  };
  return {
    spec: "run-view/1",
    historical,
    asOf: options.now,
    authority: "Self-attested observations. Applicability unknown; no permission or candidate approval is granted.",
    sections
  };
}

// packages/cli/src/run-view-command.ts
async function runViewCommand(context, args) {
  const runId = args[0];
  let json = false;
  let recordPath;
  for (let i = 1; i < args.length; i++) {
    if (args[i] === "--json" && !json) json = true;
    else if (args[i] === "--record" && recordPath === void 0 && args[i + 1])
      recordPath = args[++i];
    else
      return {
        kind: "usage",
        message: "Usage: runs view <run-id> [--json] [--record <repository-relative-path>]"
      };
  }
  if (!runId)
    return {
      kind: "usage",
      message: "Usage: runs view <run-id> [--json] [--record <repository-relative-path>]"
    };
  const resolved = await resolveRunSurface(context.rootDir);
  const read = resolved.ok ? await resolved.surface.store.read(args[0]) : void 0;
  if (!read?.ok)
    return {
      kind: "blocked",
      blockers: [
        runSurfaceBlocker({
          code: "run_view_unavailable",
          summary: "Run observations are unavailable.",
          details: resolved.ok ? "The selected run could not be read." : oneLine(resolved.reason),
          remediation: {
            id: "inspect-run",
            summary: "Check the selected repository and run identifier."
          }
        })
      ]
    };
  const view = await withRetainedRecord(
    projectRunView(read.events, { now: (/* @__PURE__ */ new Date()).toISOString() }),
    context.rootDir,
    recordPath
  );
  if (json) context.write(JSON.stringify(view));
  else {
    context.write(view.authority);
    for (const section of view.sections) {
      context.write(section.title);
      if (!section.items.length) context.write(`  ${section.empty}`);
      for (const item2 of section.items) {
        context.write(`  ${oneLine(item2.label)}`);
        for (const field2 of item2.fields)
          context.write(`    ${oneLine(field2.label)}: ${oneLine(field2.value, 8192)}`);
        if (item2.artifactId)
          context.write(
            `    Report: runs artifact ${oneLine(args[0])} ${oneLine(item2.artifactId)} --json`
          );
      }
    }
  }
  return { kind: "ok" };
}

// packages/cli/src/run-artifact-commands.ts
import {
  captureRunArtifact,
  readRunArtifact as readRunArtifact2,
  validateRunEventInput
} from "./kernel.mjs";
var blocked4 = (reason) => ({
  kind: "blocked",
  blockers: [
    runSurfaceBlocker({
      code: "run_artifact_unavailable",
      summary: "The run attachment is unavailable.",
      details: oneLine(reason),
      remediation: {
        id: "inspect-selected-report",
        summary: "Check the explicit report binding and selected source file, then retry capture."
      }
    })
  ]
});
var record2 = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
async function runArtifactCommand(context, command, args) {
  if (command === "artifact" && (args.length < 2 || args.length > 3 || args[2] !== void 0 && args[2] !== "--json"))
    return {
      kind: "usage",
      message: "Usage: runs artifact <run-id> <artifact-id> [--json]"
    };
  if (command === "capture" && (args.length !== 3 || args[1] !== "--json"))
    return {
      kind: "usage",
      message: "Usage: runs capture <run-id> --json <request>"
    };
  const resolved = await resolveRunSurface(context.rootDir);
  if (!resolved.ok) return blocked4(resolved.reason);
  const { store, commonDir } = resolved.surface;
  const runId = args[0];
  if (command === "artifact") {
    const result2 = await readRunArtifact2(store, runId, args[1]);
    if (!result2.ok) return blocked4(result2.reason);
    context.write(
      args[2] === "--json" ? `${JSON.stringify({ spec: "run-artifact/1", labels: "self-attested observation; not admission evidence", runId, ...result2 })}
` : `${oneLine(Buffer.from(result2.base64, "base64").toString("utf8"), 2 * 1024 * 1024)}
`
    );
    return { kind: "ok" };
  }
  let request;
  try {
    if (Buffer.byteLength(args[2], "utf8") > 16384)
      return blocked4("capture request exceeds 16 KiB");
    request = JSON.parse(args[2]);
  } catch {
    return { kind: "usage", message: "Capture request must be JSON." };
  }
  if (!record2(request) || Object.keys(request).sort().join(",") !== "artifact,eventId,report,sourcePath,sourceRoot" || typeof request["sourceRoot"] !== "string" || typeof request["sourcePath"] !== "string" || typeof request["eventId"] !== "string" || !record2(request["artifact"]) || !record2(request["report"]))
    return {
      kind: "usage",
      message: "Capture requires artifact, report, eventId, sourceRoot and sourcePath."
    };
  const artifact = request["artifact"];
  const report2 = request["report"];
  const binding = {
    activityId: artifact["activityId"],
    attemptId: artifact["attemptId"],
    candidateTreeSha: artifact["candidateTreeSha"],
    ...artifact["roundId"] === void 0 ? {} : { roundId: artifact["roundId"] },
    ...artifact["round"] === void 0 ? {} : { round: artifact["round"] },
    ...artifact["lensId"] === void 0 ? {} : { lensId: artifact["lensId"] }
  };
  const journal = await store.read(runId);
  if (!journal.ok || journal.events[0]?.version !== "run-event/2")
    return blocked4(
      "capture requires an existing run-event/2 run; start an explicit successor for a legacy run"
    );
  const make = (suffix, kind, payload) => {
    const event = buildRunEvent({
      runId,
      commonDir,
      kind,
      role: "executor",
      payload,
      version: "run-event/2",
      eventId: `${request["eventId"]}-${suffix}`
    });
    return event;
  };
  const artifactEvent = make("artifact", "artifact.referenced", artifact);
  const reportEvent = make("report", "report.referenced", {
    ...report2,
    ...binding,
    availability: "referenced",
    artifactId: artifact["artifactId"]
  });
  if (Object.keys(report2).some(
    (k) => !["reportId", "role", "originatingReportId", "findingId"].includes(k)
  ) || !validateRunEventInput(artifactEvent).ok || !validateRunEventInput(reportEvent).ok)
    return blocked4("invalid report or artifact event binding");
  const result = await captureRunArtifact({
    store,
    runId,
    metadata: artifact,
    sourceRoot: request["sourceRoot"],
    sourcePath: request["sourcePath"]
  });
  if (!result.ok) {
    const unavailable = make("unavailable", "report.referenced", {
      ...report2,
      ...binding,
      availability: "unavailable",
      reason: result.reason
    });
    await store.append(runId, unavailable, { reuseExistingTimestamp: true });
    return blocked4(result.reason);
  }
  for (const event of [artifactEvent, reportEvent]) {
    const appended = await store.append(runId, event, { reuseExistingTimestamp: true });
    if (!appended.ok)
      return blocked4(
        `bytes retained but reference append refused: ${appended.rejections.map((r) => r.message).join("; ")}`
      );
  }
  context.write(
    `${JSON.stringify({ spec: "run-artifact-capture/1", runId, artifactId: result.metadata.artifactId, digest: result.metadata.digest, availability: "referenced", authority: "observation" })}
`
  );
  return { kind: "ok" };
}

// packages/cli/src/commands/runs.ts
import { stat as stat3 } from "node:fs/promises";
import path20 from "node:path";
import {
  RUN_EVENT_KINDS,
  RUN_EVENT_KINDS_V1,
  RUN_EVENT_SPEC,
  RUN_EVENT_SPEC_V2,
  RUN_JOURNAL_STATUSES,
  describeRunEventPayload,
  evaluateRunJournal as evaluateRunJournal4
} from "./kernel.mjs";

// packages/cli/src/run-server.ts
import { createHash as createHash2 } from "node:crypto";

// packages/cli/src/run-live.ts
var RUN_LIVE_SCRIPT = String.raw`(() => {
  const root = document.querySelector('main');
  const toggle = document.querySelector('[data-live-toggle]');
  const status = document.querySelector('[data-live-status]');
  if (!root || !toggle || !status) return;
  let paused = false;
  let timer;
  let interactingUntil = 0;
  const interval = Number(root.dataset.pollSeconds) * 1000;
  const interact = () => { interactingUntil = Date.now() + 1500; };
  for (const event of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
    document.addEventListener(event, interact, { passive: true });
  }
  toggle.addEventListener('click', event => {
    event.preventDefault();
    paused = !paused;
    toggle.textContent = paused ? 'Resume live updates' : 'Pause updates';
    status.textContent = paused ? 'Updates paused' : 'Live';
  });
  // Preserve existing nodes wherever possible, including focused controls and
  // native disclosure state. Stable keys keep newly inserted cards from taking
  // the identity of an earlier card the operator is reading.
  const key = node => node.nodeType === 1 ? node.id || node.getAttribute('data-key') : null;
  function patch(current, next) {
    if (current.nodeType !== next.nodeType || current.nodeName !== next.nodeName) {
      current.replaceWith(next.cloneNode(true));
      return;
    }
    if (current.nodeType !== 1) {
      if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      return;
    }
    if (current.hasAttribute('data-live-controls')) return;
    for (const attr of [...current.attributes]) {
      if (current.tagName === 'DETAILS' && attr.name === 'open') continue;
      if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
    }
    for (const attr of next.attributes) {
      if (current.tagName !== 'DETAILS' || attr.name !== 'open') current.setAttribute(attr.name, attr.value);
    }
    const children = [...current.childNodes];
    const used = new Set();
    let cursor = current.firstChild;
    for (const incoming of next.childNodes) {
      const incomingKey = key(incoming);
      const existing = incomingKey
        ? children.find(child => !used.has(child) && key(child) === incomingKey)
        : children.find(child => !used.has(child) && !key(child) && child.nodeType === incoming.nodeType && child.nodeName === incoming.nodeName);
      if (existing) {
        used.add(existing);
        if (existing !== cursor) current.insertBefore(existing, cursor);
        patch(existing, incoming);
        cursor = existing.nextSibling;
      } else {
        const added = incoming.cloneNode(true);
        current.insertBefore(added, cursor);
      }
    }
    for (const child of children) if (!used.has(child)) child.remove();
  }
  const busy = () => Date.now() < interactingUntil || !window.getSelection()?.isCollapsed;
  async function poll() {
    if (paused || document.hidden || busy()) {
      timer = setTimeout(poll, interval);
      return;
    }
    try {
      const response = await fetch(location.pathname, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw Error('read');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = page.querySelector('main');
      if (!next) throw Error('read');
      if (paused || document.hidden || busy()) { timer = setTimeout(poll, interval); return; }
      const focused = document.activeElement;
      if (focused && focused !== document.body && !focused.closest('[data-live-controls]')) {
        const context = focused.closest('[id], [data-key]');
        const replacement = context && [...next.querySelectorAll('[id], [data-key]')].find(element => key(element) === key(context));
        // A completed attempt may move into collapsed history. Keep the current
        // reading snapshot until focus leaves it, rather than removing the
        // focused control or presenting a stale duplicate as current work.
        if (!replacement || context.closest('section')?.id !== replacement.closest('section')?.id) {
          status.textContent = 'New update ready · Finish reading to apply';
          timer = setTimeout(poll, interval);
          return;
        }
      }
      const anchor = [...root.querySelectorAll('[id], [data-key]')].find(element => element.getBoundingClientRect().top >= 0 && element.getBoundingClientRect().top < window.innerHeight && element.getBoundingClientRect().height > 0);
      const offset = anchor?.getBoundingClientRect().top;
      const scroll = window.scrollY;
      patch(root, next);
      if (focused?.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
      if (anchor?.isConnected && offset !== undefined) window.scrollBy(0, anchor.getBoundingClientRect().top - offset);
      else window.scrollTo(0, scroll);
      status.textContent = next.dataset.live === 'true' ? 'Live' : 'Saved observations';
      if (next.dataset.live !== 'true') { toggle.hidden = true; return; }
    } catch { status.textContent = 'Update unavailable · Retrying'; }
    timer = setTimeout(poll, interval);
  }
  timer = setTimeout(poll, interval);
  window.addEventListener('pagehide', () => clearTimeout(timer), { once: true });
})();`;

// packages/cli/src/run-view-html.ts
var escapeViewHtml = (value2) => value2.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
var text = (value2) => escapeViewHtml(oneLine(value2, 8192));
var OPERATIONAL_STYLE = `
:root{color-scheme:light dark;--paper:#f5f5f7;--surface:#fff;--ink:#1d1d1f;--muted:#626267;--line:#d8d8de;--link:#0067c0;--wash:#eaf2fa}
*{box-sizing:border-box}body{font:400 1rem/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-optical-sizing:auto;letter-spacing:-.01em;color:var(--ink);background:var(--paper);max-width:76rem;margin:0 auto;padding:2.5rem 2rem 5rem;overflow-wrap:anywhere}
h1{font-size:2.25rem;line-height:1.12;letter-spacing:-.04em;margin:0 0 1.25rem;font-weight:700}h2{font-size:1.35rem;line-height:1.3;letter-spacing:-.025em;margin:2rem 0 .5rem}h3{font-size:1.3rem;line-height:1.3;letter-spacing:-.025em;color:var(--ink);margin:0 0 1rem}h4{font-size:1.05rem;line-height:1.35;margin:0 0 1rem;letter-spacing:-.015em}
p{margin:.6rem 0 1rem}.meta,.ended{color:var(--muted);font-size:.875rem}.labels{font-size:.875rem;color:var(--muted);background:var(--surface);border:1px solid var(--line);border-radius:.75rem;padding:1rem 1.25rem;margin:1rem 0}.live{color:var(--ink)}.open{color:var(--muted)}
a{color:var(--link);text-underline-offset:.2em;border-radius:.3rem}a:hover{text-decoration-thickness:2px}a:active,summary:active{background:var(--wash);color:var(--ink)}a:focus-visible,summary:focus-visible{outline:3px solid var(--link);outline-offset:3px}nav{display:flex;flex-wrap:wrap;gap:.4rem;margin:1.5rem 0 2.5rem}nav a{display:inline-flex;align-items:center;min-height:2.75rem;padding:.5rem .85rem;border:1px solid var(--line);border-radius:2rem;background:var(--surface);font-size:.875rem;text-decoration:none}nav a:hover{border-color:var(--link)}
section{scroll-margin-top:1.5rem;margin:2.5rem 0}section>p{color:var(--muted);font-size:.9375rem}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,23rem),1fr));gap:1rem}.card{background:var(--surface);border:1px solid var(--line);border-radius:1rem;padding:1.5rem;min-width:0}.card>a{display:inline-flex;align-items:center;min-height:2.75rem;margin-top:.5rem}dl{margin:.5rem 0}dt{font-size:.8125rem;color:var(--muted);font-weight:500;margin-top:.75rem}dd{margin:.15rem 0 .75rem;overflow-wrap:anywhere}details{margin:1rem 0;border-top:1px solid var(--line);padding-top:.5rem}summary{cursor:pointer;min-height:2.75rem;padding:.5rem 0;font-size:.875rem;color:var(--muted);border-radius:.3rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--surface);border:1px solid var(--line);border-radius:1rem;padding:1.5rem;font: .875rem/1.65 ui-monospace,SFMono-Regular,Menlo,monospace}.table-scroll{max-width:100%;overflow-x:auto;margin:1rem 0 2rem;border:1px solid var(--line);border-radius:.75rem}table{border-collapse:collapse;display:table;min-width:48rem;width:100%;font-size:.8125rem;margin:0}th,td{border:0;border-bottom:1px solid var(--line);padding:.75rem;text-align:left;vertical-align:top}th{background:var(--surface);font-weight:600}tr:last-child td{border-bottom:0}
.back{margin:.5rem 0}.run-identifiers,.view-provenance{margin:.25rem 0;border:0;padding:0}.section-navigation{margin:.25rem 0 1rem;border:0;padding:0}.section-navigation nav{margin:.5rem 0}body>details:first-of-type{margin:.5rem 0;border:0;padding:0}body>h1{margin-bottom:.5rem}section:first-of-type{margin-top:1.25rem}
@media(max-width:600px){body{padding:1.5rem 1rem 3rem;margin:0}h1{font-size:1.875rem}.cards{grid-template-columns:1fr}.card{padding:1.125rem}nav{gap:.375rem}section{margin:2rem 0}}
@media(prefers-color-scheme:dark){:root{--paper:#161618;--surface:#212124;--ink:#f5f5f7;--muted:#b1b1b8;--line:#45454b;--link:#8ac7ff;--wash:#263b50}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
@media(prefers-reduced-transparency:reduce){.card,nav a,.labels{background:var(--surface);backdrop-filter:none}}
@media(prefers-contrast:more){:root{--muted:var(--ink);--line:var(--ink)}a{text-decoration:underline}nav a{border-width:2px}}
body{max-width:68rem;padding-top:2rem}main{min-width:0}.page-header{margin:2rem 0}.page-header h1{margin:.4rem 0 .75rem}.eyebrow{font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;font-weight:600;color:var(--muted);margin:0 0 .5rem}.toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:1rem;font-size:.8125rem;margin:1rem 0 2rem}.toolbar a{min-height:2.75rem;display:inline-flex;align-items:center}.toolbar .meta{margin-right:auto}.status{display:inline-flex;padding:.3rem .65rem;background:var(--wash);border-radius:2rem;font-size:.8125rem;font-weight:600}.run-list{display:grid;gap:.75rem}.run-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.75rem 2rem;background:var(--surface);border:1px solid var(--line);border-radius:1rem;padding:1.25rem 1.5rem}.run-row h3{font-size:1.125rem;margin:0}.run-row h3 a{display:inline-flex;align-items:center;min-height:2.75rem}.run-row p{margin:.25rem 0}.run-row .run-caption{grid-column:1/-1;font-size:.8125rem;color:var(--muted)}.now-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}.now-grid section{margin:0}.now-grid section h2{margin-top:.5rem}.now-grid .cards{grid-template-columns:1fr}.empty-note{margin:0;padding:1rem 0;font-size:.875rem}.supporting{margin-top:3rem}.supporting>summary{font-size:1rem;font-weight:600;color:var(--ink)}summary span{display:block;font-size:.8125rem;font-weight:400;color:var(--muted);margin:.25rem 0}.section-detail>section{margin-top:.5rem}.card{padding:1.25rem}.card h3{font-size:1rem;margin-bottom:.75rem}.card dl{display:grid;grid-template-columns:7rem minmax(0,1fr);gap:.35rem 1rem;margin:0}.card dt,.card dd{margin:0;font-size:.875rem}.card details{margin-bottom:0}.card details dl{display:block}.card details dt{margin-top:.75rem}.card details dd{margin-top:.2rem}.report-card{border-top:3px solid var(--line)}.report-link{font-weight:500;gap:.5rem}.notice{border-left:3px solid var(--line);padding:.75rem 1rem;background:var(--surface);border-radius:.25rem}.report-page{max-width:54rem}.report-document{background:var(--surface);border:1px solid var(--line);border-radius:1.25rem;padding:2.5rem}.report-document header h2{font-size:1.8rem;margin:.5rem 0 1rem}.report-document p{line-height:1.7;white-space:pre-wrap}.report-document pre{padding:0;border:0}.report-document section{margin:2rem 0}.report-document h3{font-size:1.2rem;line-height:1.4}.finding{border-top:1px solid var(--line);padding:1.75rem 0 .5rem}.finding h4{margin:1.5rem 0 .5rem}.count{font-size:.875rem;font-weight:400;color:var(--muted);margin-left:.5rem}.document-fields>dt{color:var(--ink);font-weight:600;font-size:.875rem;margin-top:1.5rem}.document-fields dd p{margin-top:.25rem}.document-list{padding-left:1.25rem}.document-list li{margin:.75rem 0}.report-tools{margin:1.5rem 0}.report-tools a{display:inline-flex;align-items:center;min-height:2.75rem}
@media(max-width:600px){.now-grid{grid-template-columns:1fr;gap:.5rem}.run-row{padding:1rem;gap:.5rem}.run-row .status{align-self:start}.card dl{grid-template-columns:5.5rem minmax(0,1fr)}.card dt{margin-top:0}.report-document{padding:1.25rem}.page-header{margin:1.5rem 0}.toolbar{gap:.5rem 1rem}}
`;
var field = (item2, label2) => item2.fields.find((f) => f.label === label2)?.value;
var friendlyLabels = /* @__PURE__ */ new Map([
  ["lens.outcome-correctness", "Outcome review"],
  ["lens.adversarial-testing", "Testing review"],
  ["review", "Review report"],
  ["reduction", "Review summary"],
  ["clarification", "Review clarification"],
  ["partial-output", "Partial report"]
]);
var friendly = (value2) => friendlyLabels.get(value2) ?? value2;
var primaryLabels = {
  waiting: ["Owner", "Human action required", "Next action", "Freshness"],
  work: ["Owner", "State", "Freshness", "Next step"],
  reviews: ["Round", "Owner", "State", "Verdict", "Freshness", "Next step"],
  reports: ["Availability", "Reason"],
  finish: ["State", "Owner"],
  findings: ["State", "Severity", "Deferred issue"]
};
function fieldsHtml(fields) {
  return fields.map((f) => `<dt>${text(f.label)}</dt><dd>${text(f.value)}</dd>`).join("");
}
function card(item2, section, baseHref) {
  const labels = primaryLabels[section];
  const primary = item2.fields.filter((f) => labels ? labels.includes(f.label) && f.value !== "Unreported" && !(section === "reports" && f.label === "Availability" && f.value === "referenced") : true);
  const supporting = item2.fields.filter((f) => !primary.includes(f));
  const title = section === "reports" ? friendly(field(item2, "Lens") === "Unreported" ? item2.label : field(item2, "Lens") ?? item2.label) : section === "work" ? label(field(item2, "Phase") ?? "Delivery activity") : friendly(item2.label);
  return `<article data-key="${text(item2.id)}" class="card ${section === "reports" ? "report-card" : ""}"><h3>${text(title)}</h3><dl>${fieldsHtml(primary)}</dl>${item2.artifactId === void 0 ? "" : `<a class="report-link" href="${text(baseHref)}/artifacts/${encodeURIComponent(item2.artifactId)}">Read report <span aria-hidden="true">\u2197</span></a>`}${supporting.length ? `<details data-key="support-${text(item2.id)}"><summary>Supporting details</summary><dl>${section === "work" ? `<dt>Activity</dt><dd>${text(item2.label)}</dd>` : ""}${fieldsHtml(supporting)}</dl></details>` : ""}</article>`;
}
var sectionTitles = { waiting: "Needs attention", work: "In progress", reviews: "Reviews", reports: "Review reports", finish: "Delivery milestones" };
var emptyCopy = { waiting: "No waits reported.", reviews: "No current reviews reported.", reports: "Review reports will appear here when they are recorded.", finish: "Delivery milestones have not been reported." };
function sectionHtml(section, baseHref) {
  return `<section id="${text(section.id)}"><h2>${text(sectionTitles[section.id] ?? section.title)}</h2>${section.items.length ? `<div class="cards">${section.items.map((i) => card(i, section.id === "earlier-report-list" ? "reports" : section.id, baseHref)).join("")}</div>` : `<p class="empty-note">${text(emptyCopy[section.id] ?? section.empty)}</p>`}</section>`;
}
function renderOperationalView(view, baseHref) {
  const sections = new Map(view.sections.map((s) => [s.id, s]));
  const render = (id) => sections.has(id) ? sectionHtml(sections.get(id), baseHref) : "";
  const reviews = sections.get("reviews");
  const work = sections.get("work");
  const currentReviews = reviews?.items.filter((i) => i.label !== "Declared review round" && field(i, "History") !== "Superseded attempt") ?? [];
  const reviewIds = new Set(currentReviews.map((i) => i.id));
  const reviewCards = currentReviews.map((i) => ({ ...i, fields: [...i.fields, ...work?.items.find((w) => w.id === i.id)?.fields.filter((f) => ["Freshness", "Next step"].includes(f.label)) ?? []] }));
  if (work) sections.set("work", { ...work, items: work.items.filter((i) => !reviewIds.has(i.id)), empty: work.items.some((i) => reviewIds.has(i.id)) ? "Reviews are shown below." : work.empty });
  const reports = sections.get("reports");
  const currentReports = reports?.items.filter((i) => field(i, "History") === "Latest observed attempt") ?? [];
  const earlierReports = reports?.items.filter((i) => !currentReports.includes(i)) ?? [];
  const pastReviews = reviews?.items.filter((i) => !currentReviews.includes(i)) ?? [];
  return `${view.historical ? '<p class="notice">Historical archive \xB7 Observations retained at export.</p>' : ""}
    <div class="now-grid">${render("waiting")}${render("work")}</div>
    ${sections.get("findings")?.items.length ? render("findings") : ""}
    ${reviews ? sectionHtml({ ...reviews, items: reviewCards }, baseHref) : ""}
    ${reports ? sectionHtml({ ...reports, items: currentReports }, baseHref) : ""}
    ${earlierReports.length && reports ? `<details id="earlier-reports"><summary>Earlier reports <span>${earlierReports.length} reports from earlier attempts or incomplete references</span></summary>${sectionHtml({ ...reports, id: "earlier-report-list", title: "Earlier reports", items: earlierReports }, baseHref)}</details>` : ""}${render("finish")}
    <details id="supporting-evidence" class="supporting"><summary>Evidence, cost and history<span>Supporting facts and earlier observations</span></summary>
    ${pastReviews.length && reviews ? sectionHtml({ ...reviews, id: "review-history", title: "Earlier reviews and round details", items: pastReviews }, baseHref) : ""}
    ${["evidence", "cost", "activity-history", "finding-history"].map((id) => `<details id="details-${id}" class="section-detail"><summary>${text(sections.get(id)?.title ?? id)} <span>${sections.get(id)?.items.length ?? 0} entries</span></summary>${render(id)}</details>`).join("")}
    ${!sections.get("findings")?.items.length ? render("findings") : ""}</details>`;
}
var record3 = (value2) => typeof value2 === "object" && value2 !== null && !Array.isArray(value2);
var label = (key) => key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_-]/g, " ").replace(/^./, (c) => c.toUpperCase());
function documentValue(value2, depth = 0) {
  if (depth > 4) return '<p class="meta">Further detail is available in the original report.</p>';
  if (Array.isArray(value2)) return value2.length ? `<ul class="document-list">${value2.slice(0, 40).map((v) => `<li>${documentValue(v, depth + 1)}</li>`).join("")}</ul>${value2.length > 40 ? '<p class="meta">Additional entries are available in the original report.</p>' : ""}` : '<p class="meta">None reported.</p>';
  if (record3(value2)) return `<dl class="document-fields">${Object.entries(value2).slice(0, 40).map(([k, v]) => `<dt>${text(label(k))}</dt><dd>${documentValue(v, depth + 1)}</dd>`).join("")}</dl>${Object.keys(value2).length > 40 ? '<p class="meta">Additional fields are available in the original report.</p>' : ""}`;
  return `<p>${text(value2 === null ? "Not reported" : String(value2))}</p>`;
}
function structuredReport(source) {
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch {
    return void 0;
  }
  if (!record3(parsed)) return void 0;
  const result = parsed["outcome"] ?? parsed["verdict"] ?? parsed["decision"];
  const findings = parsed["findings"];
  const headline = result === "aligned" ? "Aligned" : result === "dissent" || result === "changes-requested" || result === "CHANGES_REQUESTED" ? "Changes requested" : typeof result === "string" ? result : "Report contents";
  const summary = parsed["summary"] ?? parsed["assessment"];
  const rest = Object.fromEntries(Object.entries(parsed).filter(([k]) => !["outcome", "verdict", "decision", "findings", "summary", "assessment"].includes(k)));
  return `<article class="report-document"><header><p class="eyebrow">Reported result</p><h2>${text(headline)}</h2>${summary === void 0 ? "" : documentValue(summary)}</header>
    ${Array.isArray(findings) ? `<section><h2>Findings <span class="count">${findings.length}</span></h2>${findings.length === 0 ? "<p>No findings reported in this document.</p>" : findings.slice(0, 40).map((finding, index) => {
    if (!record3(finding)) return documentValue(finding);
    const title = typeof finding["title"] === "string" ? finding["title"] : `Finding ${index + 1}`;
    const why = finding["why_it_matters"] ?? finding["description"];
    const fix = finding["suggested_fix"];
    const extra = Object.fromEntries(Object.entries(finding).filter(([k]) => !["title", "severity", "why_it_matters", "description", "suggested_fix"].includes(k)));
    return `<article class="finding"><p class="eyebrow">${text(typeof finding["severity"] === "string" ? finding["severity"] : "Severity unreported")}</p><h3>${text(title)}</h3>${why === void 0 ? "" : documentValue(why)}${fix === void 0 ? "" : `<h4>Recommended change</h4>${documentValue(fix)}`}<details><summary>Evidence and context</summary>${documentValue(extra)}</details></article>`;
  }).join("")}${findings.length > 40 ? "<p>Additional findings are available in the original report.</p>" : ""}</section>` : findings === void 0 ? "" : `<section><h2>Findings</h2>${documentValue(findings)}</section>`}
    <details><summary>Review context and supporting evidence</summary>${documentValue(rest)}</details></article>`;
}
function renderArtifactDetail(input) {
  const { result } = input;
  const m = result.ok ? result.metadata : input.metadata;
  const source = result.ok ? Buffer.from(result.base64, "base64").toString("utf8") : "";
  const document = result.ok ? structuredReport(source) : void 0;
  const title = friendly(m?.lensId ?? "Review report");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${text(title)} \xB7 Delivery runs</title><style>${OPERATIONAL_STYLE}</style></head><body class="report-page"><main><a class="back" href="${text(input.backHref)}#reports">Return to run</a><header class="page-header"><p class="eyebrow">${input.historical ? "Historical archive" : "Retained report"}${m?.round === void 0 ? "" : ` \xB7 Round ${m.round}`}</p><h1>${text(title)}</h1><p class="meta">Reported review output; not approval evidence.</p></header>
    ${result.ok ? `${document ?? `<article class="report-document"><h2>Report contents</h2><pre>${escapeViewHtml(source)}</pre></article>`}<div class="report-tools"><a href="${text(input.backHref)}/artifacts/${encodeURIComponent(input.artifactId)}/download">Download original report</a></div>${document ? `<details><summary>Original report source</summary><pre>${escapeViewHtml(source)}</pre></details>` : ""}` : `<section class="notice" role="status"><h2>Report unavailable</h2><p>${text(result.reason)}</p><p class="meta">${text(result.code)}</p></section>`}
    <details><summary>Report details and provenance</summary><dl><dt>Run</dt><dd>${text(input.runId)}</dd><dt>Artifact</dt><dd>${text(input.artifactId)}</dd>${m ? `<dt>Lens</dt><dd>${text(m.lensId ?? "Unreported")}</dd><dt>Attempt</dt><dd>${text(m.attemptId)}</dd><dt>Candidate</dt><dd>${text(m.candidateTreeSha)}</dd><dt>Digest</dt><dd>${text(m.digest)}</dd><dt>Size</dt><dd>${m.sizeBytes} bytes</dd>` : ""}</dl></details></main></body></html>`;
}

// packages/cli/src/run-server.ts
import {
  createServer
} from "node:http";
import {
  evaluateRunJournal as evaluateRunJournal3,
  sha256Hex as sha256Hex7,
  readRunArtifact as readRunArtifact3,
  RUN_STORE_ID as RUN_STORE_ID3
} from "./kernel.mjs";
var RUN_SERVER_HOST = "127.0.0.1";
var DEFAULT_POLL_SECONDS = 2;
var RUN_SERVER_CSP = [
  "default-src 'none'",
  "script-src 'none'",
  "style-src 'unsafe-inline'",
  "img-src 'none'",
  "connect-src 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
  "base-uri 'none'"
].join("; ");
async function resolveRepo(repoPath) {
  const surface = await resolveRunSurface(repoPath);
  if (!surface.ok) return { reason: `${repoPath}: ${surface.reason}` };
  const root = await resolveWorktreeRoot(repoPath);
  if (!root.ok) return { reason: `${repoPath}: ${root.reason}` };
  return {
    root: root.root,
    commonDir: surface.surface.commonDir,
    runsDir: surface.surface.runsDir,
    worktreeKey: surface.surface.worktreeKey,
    store: surface.surface.store
  };
}
function groupByStore(resolved) {
  const groups = /* @__PURE__ */ new Map();
  for (const repo of resolved) {
    const existing = groups.get(repo.commonDir);
    if (existing === void 0) {
      groups.set(repo.commonDir, {
        root: repo.root,
        commonDir: repo.commonDir,
        runsDir: repo.runsDir,
        worktreeKeys: [repo.worktreeKey],
        store: repo.store
      });
      continue;
    }
    if (!existing.worktreeKeys.includes(repo.worktreeKey))
      existing.worktreeKeys.push(repo.worktreeKey);
  }
  return [...groups.values()];
}
function servedRun(input) {
  const { summary } = input;
  return {
    runId: input.runId,
    ...input.view === void 0 ? {} : { view: input.view },
    ...input.href === void 0 ? {} : { href: input.href },
    repository: input.repository,
    readable: input.readable,
    live: input.live,
    open: summary.open,
    ticket: summary.ticket,
    startedAt: summary.startedAt,
    lastAt: summary.lastAt,
    durationSeconds: summary.durationSeconds,
    rounds: { opened: summary.roundsOpened, closed: summary.roundsClosed },
    findings: summary.findings,
    ...summary.gate === void 0 ? {} : { gate: summary.gate },
    ...summary.record === void 0 ? {} : { record: summary.record },
    ...summary.result === void 0 ? {} : { result: summary.result },
    readout: input.readout,
    timeline: input.timeline,
    roundDetail: input.roundDetail,
    notes: input.notes
  };
}
var EMPTY_SUMMARY = {
  ticket: "",
  open: true,
  startedAt: "",
  lastAt: "",
  durationSeconds: 0,
  // Nothing was read, so every phase is empty and the gate is unseen rather
  // than summed to zero: an unreadable journal reports no gate, not a free one.
  phases: {
    implementationSeconds: 0,
    reviewSeconds: 0,
    tailSeconds: 0,
    rounds: 0,
    gate: { totalMs: 0, counted: 0, unreadable: 0, unseen: true }
  },
  roundsOpened: 0,
  roundsClosed: 0,
  findings: { P0: 0, P1: 0, P2: 0, P3: 0 }
};
function timelineOf(events) {
  return events.map((event) => ({
    seq: event.seq,
    at: event.at,
    kind: event.kind,
    writer: event.actor.role,
    detail: detailOf(event)
  }));
}
function roundsOf(events) {
  return roundEntries(events).map((entry) => {
    const closed = entry.closed === void 0 ? void 0 : payloadOf(entry.closed);
    return {
      round: entry.round,
      candidateTreeSha: entry.candidateTreeSha,
      opened: entry.opened !== void 0,
      lenses: roundLenses(entry),
      outcome: closed === void 0 ? "open" : oneLineOf(closed["outcome"], 64),
      findings: closed === void 0 ? "" : oneLineOf(closed["findings"]),
      cost: closed === void 0 ? "" : costLabel(closed["cost"])
    };
  });
}
function notesOf(entries) {
  return entries.map((entry) => {
    const note = typeof entry === "object" && entry !== null ? entry : {};
    return {
      at: oneLineOf(note["at"], 32),
      kind: oneLineOf(note["kind"], 128),
      code: oneLineOf(note["code"], 64),
      pattern: oneLineOf(note["pattern"], 64)
    };
  });
}
async function readState(groups, pollSeconds, now, freshnessWindowMs, recordPath) {
  const runs = [];
  for (const group of groups) {
    const live = /* @__PURE__ */ new Set();
    for (const worktreeKey of group.worktreeKeys) {
      const current = await group.store.current(worktreeKey);
      if (current.ok && current.runId !== void 0) live.add(current.runId);
    }
    for (const runId of await group.store.list()) {
      const read = await group.store.read(runId);
      const notes = notesOf(await group.store.readNotes(runId));
      if (!read.ok) {
        runs.push(
          servedRun({
            runId,
            repository: group.root,
            readable: false,
            href: `/runs/${sha256Hex7(group.commonDir)}/${runId}`,
            live: false,
            summary: EMPTY_SUMMARY,
            readout: {
              status: "absent",
              present: [],
              missing: [],
              violations: []
            },
            timeline: [],
            roundDetail: [],
            notes
          })
        );
        continue;
      }
      const events = read.events;
      const summary = summarize(events);
      runs.push(
        servedRun({
          runId,
          repository: group.root,
          view: await withRetainedRecord(
            projectRunView(events, {
              now,
              ...freshnessWindowMs === void 0 ? {} : { freshnessWindowMs }
            }),
            group.root,
            recordPath
          ),
          href: `/runs/${sha256Hex7(group.commonDir)}/${runId}`,
          readable: true,
          // Liveness is the pointer AND the absence of an end, never one alone:
          // a pointer left behind by a run that ended without clearing it must
          // not read as a run still in flight.
          live: summary.open && live.has(runId),
          summary,
          // No record tree sha and no mandated pair: the viewer has neither, and
          // pretending otherwise would turn an observation into a claim.
          readout: readoutOf(events, evaluateRunJournal3(events), group.root),
          timeline: timelineOf(events),
          roundDetail: roundsOf(events),
          notes
        })
      );
    }
  }
  return {
    labels: READOUT_LABELS,
    pollSeconds,
    repositories: groups.map((group) => ({
      root: group.root,
      commonDir: group.commonDir,
      runsDir: group.runsDir,
      worktreeKeys: group.worktreeKeys
    })),
    runs
  };
}
function escapeHtml(value2) {
  return value2.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
var cell = (value2, maximum = 240) => escapeHtml(oneLine(value2, maximum));
function runStatus(run) {
  if (!run.readable) return "Unable to read";
  if (run.result !== void 0) return `Reported ${run.result}`;
  return run.open ? "Open" : "Ended";
}
function runsTable(state) {
  if (!state.runs.length) return '<section class="notice"><h2>No deliveries yet</h2><p>Runs will appear here when a delivery is recorded in a connected repository.</p></section>';
  const rows = (runs) => `<div class="run-list">${runs.map((run) => `<article data-key="${escapeHtml(run.href ?? run.runId)}" class="run-row"><div><h3><a href="${escapeHtml(run.href ?? "#")}">${cell(run.ticket || run.runId, 128)}</a></h3><p class="meta">${cell(run.repository.split("/").filter(Boolean).at(-1) ?? run.repository, 128)}</p></div><div><span class="status">${cell(runStatus(run), 128)}</span></div><p class="run-caption">${run.readable ? `${run.rounds.closed} of ${run.rounds.opened} review rounds closed \xB7 Last reported ${cell(run.lastAt || "unknown", 32)}` : "Activity and results are unavailable."}</p></article>`).join("")}</div>`;
  const ordered = [...state.runs].sort((a, b) => b.lastAt.localeCompare(a.lastAt));
  const open3 = ordered.filter((run) => run.open);
  const ended = ordered.filter((run) => !run.open);
  return `${open3.length ? `<section id="open-runs"><h2>Open deliveries <span class="count">${open3.length}</span></h2>${rows(open3.slice(0, 10))}${open3.length > 10 ? `<details id="older-open-runs"><summary>Earlier open deliveries <span>${open3.length - 10} runs</span></summary>${rows(open3.slice(10))}</details>` : ""}</section>` : ""}${ended.length ? `<section id="completed-runs"><h2>Recent deliveries</h2>${rows(ended.slice(0, 6))}${ended.length > 6 ? `<details id="older-ended-runs"><summary>Earlier deliveries <span>${ended.length - 6} runs</span></summary>${rows(ended.slice(6))}</details>` : ""}</section>` : ""}`;
}
function timelineTable(run) {
  const rows = run.timeline.map(
    (entry) => [
      "<tr>",
      `<td>${entry.seq}</td>`,
      `<td>${cell(entry.at, 32)}</td>`,
      `<td>${cell(entry.kind, 64)}</td>`,
      `<td>${cell(entry.writer, 16)}-written</td>`,
      `<td>${cell(entry.detail, 400)}</td>`,
      "</tr>"
    ].join("")
  );
  return [
    "<h3>timeline</h3><table><tr><th>seq</th><th>at</th><th>kind</th><th>writer</th><th>detail</th></tr>",
    rows.length === 0 ? '<tr><td colspan="5">no readable events</td></tr>' : rows.join(""),
    "</table>"
  ].join("");
}
function roundsTable(run) {
  if (run.roundDetail.length === 0) return "";
  const rows = run.roundDetail.map(
    (round) => [
      "<tr>",
      `<td>${cell(round.round, 32)}</td>`,
      `<td>${cell(round.candidateTreeSha, 128) || "(none)"}</td>`,
      `<td>${cell(round.lenses)}</td>`,
      `<td>${cell(round.outcome, 64)}</td>`,
      `<td>${cell(round.findings)}</td>`,
      `<td>${cell(round.cost, 64)}</td>`,
      "</tr>"
    ].join("")
  );
  return [
    "<h3>rounds</h3><table><tr><th>round</th><th>candidate</th><th>lenses</th><th>outcome</th><th>findings</th><th>cost</th></tr>",
    rows.join(""),
    "</table>"
  ].join("");
}
function notesTable(run) {
  if (run.notes.length === 0) return "";
  const rows = run.notes.map(
    (note) => `<tr><td>${cell(note.at, 32)}</td><td>${cell(note.kind, 128)}</td><td>${cell(note.code, 64)}</td><td>${cell(note.pattern, 64)}</td></tr>`
  );
  return [
    "<h3>refused appends</h3><table><tr><th>at</th><th>kind</th><th>code</th><th>pattern</th></tr>",
    rows.join(""),
    "</table>"
  ].join("");
}
function readoutBlock(run) {
  const list = (entries) => entries.length === 0 ? "(none)" : cell(entries.join(", "), 800);
  return [
    "<h3>completeness</h3>",
    `<p class="labels">${cell(run.readout.status, 64)} \u2014 ${escapeHtml(READOUT_LABELS)}</p>`,
    `<p class="meta">present: ${list(run.readout.present)}</p>`,
    `<p class="meta">missing: ${list(run.readout.missing)}</p>`,
    run.readout.violations.length === 0 ? "" : `<p class="meta">violations: ${list(run.readout.violations)}</p>`,
    run.readout.note === void 0 ? "" : `<p class="meta">note: ${cell(run.readout.note, 500)}</p>`
  ].join("");
}
function renderPage(state) {
  const anyLive = state.runs.some((run) => run.live);
  const refresh = anyLive;
  const selected = state.selected ? state.runs[0] : void 0;
  const base = selected?.href ?? "/";
  const html = [
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
    `<title>${selected ? cell(selected.ticket || "Delivery run", 128) : "Delivery runs"}</title><style>${OPERATIONAL_STYLE}</style></head><body><main data-live="${anyLive}" data-poll-seconds="${state.pollSeconds}">`,
    selected ? '<a class="back" href="/">All runs</a>' : "",
    `<header class="page-header"><p class="eyebrow">Delivery workspace</p><h1>${selected ? cell(selected.ticket || "Delivery run", 128) : "Delivery runs"}</h1>`,
    selected ? `<span class="status">${cell(runStatus(selected), 128)}</span>` : '<p class="meta">Follow delivery progress and read the latest reviews.</p>',
    '</header><div class="toolbar" data-live-controls>',
    `<span class="meta" data-live-status>${refresh ? "Live" : "Saved observations"}</span>`,
    `<a href="${escapeHtml(base)}">Refresh</a>`,
    anyLive ? `<a data-live-toggle href="${escapeHtml(base)}">Pause updates</a>` : "",
    "</div>",
    selected ? !selected.readable ? `<section class="notice" aria-label="Run read error"><h2>Run journal unreadable</h2><p>The journal for ${cell(selected.runId, 128)} could not be read. Activity, evidence and completeness are unavailable.</p></section>` : selected.view ? renderOperationalView(selected.view, selected.href ?? "") : "" : runsTable(state),
    selected?.readable ? `<details id="journal-details" class="supporting"><summary>Journal details<span>Recorded events, round accounting and completeness</span></summary>${roundsTable(selected)}${timelineTable(selected)}${notesTable(selected)}${readoutBlock(selected)}</details>` : "",
    '<details id="view-provenance" class="view-provenance"><summary>About these observations</summary>',
    `<p class="meta">Reported observations only; not approval evidence. ${escapeHtml(READOUT_LABELS)}. Nothing here is read by admission, the gate, or the recorder.</p>`,
    ...state.repositories.map((repository) => `<p class="meta">${cell(repository.root, 400)} \u2014 ${cell(repository.runsDir, 400)}</p>`),
    selected ? `<p class="meta">Run ${cell(selected.runId, 128)} \xB7 Last reported ${cell(selected.lastAt || "unknown", 32)}</p>` : "",
    anyLive ? `<p class="meta">Live mode refreshes every ${state.pollSeconds}s while a run is selected and open; execution is not inferred.</p>` : '<p class="meta">No selected open run; this page does not refresh itself.</p>',
    `</details></main>${refresh ? `<script>${RUN_LIVE_SCRIPT}</script>` : ""}</body></html>`
  ].join("").replace(/<table>/g, '<div class="table-scroll"><table>').replace(/<\/table>/g, "</table></div>");
  return html;
}
var SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy": RUN_SERVER_CSP,
  "Referrer-Policy": "no-referrer",
  // A viewer of a live store must never be shown a cached run, and no proxy
  // between loopback and loopback has any business holding one.
  "Cache-Control": "no-store"
};
var LIVE_CSP = RUN_SERVER_CSP.replace("script-src 'none'", `script-src 'sha256-${createHash2("sha256").update(RUN_LIVE_SCRIPT).digest("base64")}'`).replace("connect-src 'none'", "connect-src 'self'");
function send(response, status, contentType, body, livePage = false) {
  response.writeHead(status, {
    ...SECURITY_HEADERS,
    "Content-Type": contentType,
    ...livePage ? { "Content-Security-Policy": LIVE_CSP } : {}
  });
  response.end(body);
}
function hostIsBound(header, host, port) {
  return header === `${host}:${port}`;
}
async function startRunServer(input) {
  if (input.repos.length === 0 && (input.archives?.length ?? 0) === 0)
    return { ok: false, reason: "no repository path to serve" };
  const pollSeconds = input.pollSeconds ?? DEFAULT_POLL_SECONDS;
  if (!Number.isFinite(pollSeconds) || pollSeconds <= 0 || !Number.isFinite(input.freshnessWindowMs ?? 0) || (input.freshnessWindowMs ?? 0) < 0)
    return {
      ok: false,
      reason: "invalid observation refresh or freshness window"
    };
  const resolved = [];
  for (const repoPath of input.repos) {
    const outcome2 = await resolveRepo(repoPath);
    if ("reason" in outcome2) return { ok: false, reason: outcome2.reason };
    resolved.push(outcome2);
  }
  const groups = groupByStore(resolved);
  const archives = /* @__PURE__ */ new Map();
  for (const supplied of input.archives ?? []) {
    const parsed = parseRunExport(supplied.text);
    if (!parsed.ok)
      return { ok: false, reason: "archive invalid or unsupported" };
    archives.set(sha256Hex7(supplied.text), {
      ...supplied,
      archive: parsed.value
    });
  }
  const now = input.now ?? (() => (/* @__PURE__ */ new Date()).toISOString());
  const archiveRun = (id, entry) => servedRun({
    runId: entry.archive.runId,
    repository: `Archive: ${entry.label}`,
    readable: true,
    live: false,
    summary: summarize(entry.archive.events),
    readout: entry.archive.readout,
    timeline: timelineOf(entry.archive.events),
    roundDetail: roundsOf(entry.archive.events),
    notes: [],
    href: `/archives/${id}`,
    view: projectRunView(entry.archive.events, {
      now: entry.archive.events.at(-1)?.at ?? "",
      historical: true
    })
  });
  const state = async () => {
    const live = await readState(
      groups,
      pollSeconds,
      now(),
      input.freshnessWindowMs,
      input.recordPath
    );
    return {
      ...live,
      runs: [
        ...live.runs,
        ...[...archives.entries()].map(([id, entry]) => archiveRun(id, entry))
      ]
    };
  };
  let bound2;
  const server = createServer(
    (request, response) => {
      void (async () => {
        try {
          if (bound2 === void 0 || !hostIsBound(request.headers.host, bound2.host, bound2.port)) {
            send(
              response,
              403,
              "text/plain; charset=utf-8",
              "forbidden host\n"
            );
            return;
          }
          if (request.method !== "GET" && request.method !== "HEAD") {
            send(
              response,
              405,
              "text/plain; charset=utf-8",
              "method not allowed\n"
            );
            return;
          }
          const requestUrl = new URL(request.url ?? "/", "http://127.0.0.1");
          const route = requestUrl.pathname;
          if (route === "/") {
            send(
              response,
              200,
              "text/html; charset=utf-8",
              renderPage(await state()),
              true
            );
            return;
          }
          if (route === "/api/runs") {
            send(
              response,
              200,
              "application/json; charset=utf-8",
              `${JSON.stringify(await state(), null, 2)}
`
            );
            return;
          }
          const match = route?.match(
            /^\/(runs\/([a-f0-9]{64})\/([A-Za-z0-9_-]+)|archives\/([a-f0-9]{64}))(?:\/artifacts\/([A-Za-z0-9_-]+)(\/download)?)?$/
          );
          if (match) {
            const base = `/${match[1]}`;
            const archive = match[4] ? archives.get(match[4]) : void 0;
            const group = match[2] ? groups.find((g) => sha256Hex7(g.commonDir) === match[2]) : void 0;
            if (!archive && !group) {
              send(response, 404, "text/plain; charset=utf-8", "not found\n");
              return;
            }
            const runId = archive?.archive.runId ?? match[3];
            const artifactId = match[5];
            if (artifactId !== void 0) {
              if (!RUN_STORE_ID3.test(artifactId) || artifactId.length > 128) {
                send(response, 404, "text/plain; charset=utf-8", "not found\n");
                return;
              }
              const result = archive ? readArchiveArtifact(archive.text, artifactId) : await readRunArtifact3(group.store, runId, artifactId);
              if (match[6] && result.ok) {
                response.writeHead(200, {
                  ...SECURITY_HEADERS,
                  "Content-Type": "application/octet-stream",
                  "Content-Disposition": `attachment; filename="${artifactId}.bin"`
                });
                response.end(Buffer.from(result.base64, "base64"));
                return;
              }
              const liveRead = group === void 0 ? void 0 : await group.store.read(runId);
              const referenceList = archive?.archive.events ?? (liveRead?.ok ? liveRead.events : []);
              const reference = referenceList.find(
                (e) => e.kind === "artifact.referenced" && e.payload["artifactId"] === artifactId
              )?.payload;
              send(
                response,
                result.ok ? 200 : 404,
                "text/html; charset=utf-8",
                renderArtifactDetail({
                  runId,
                  artifactId,
                  backHref: base,
                  ...reference === void 0 ? {} : { metadata: reference },
                  result,
                  historical: archive !== void 0
                })
              );
              return;
            }
            const full = await state();
            const selected = full.runs.filter((r) => r.href === base);
            if (!selected.length) {
              send(response, 404, "text/plain; charset=utf-8", "not found\n");
              return;
            }
            send(
              response,
              200,
              "text/html; charset=utf-8",
              renderPage({ ...full, runs: selected, selected: true }),
              true
            );
            return;
          }
          send(response, 404, "text/plain; charset=utf-8", "not found\n");
        } catch {
          if (!response.headersSent)
            send(
              response,
              500,
              "text/plain; charset=utf-8",
              "the run store could not be read\n"
            );
          response.end();
        }
      })();
    }
  );
  const listening = await new Promise((resolve) => {
    server.once("error", (error) => resolve(error.message));
    server.listen(input.port ?? 0, RUN_SERVER_HOST, () => resolve(void 0));
  });
  if (listening !== void 0)
    return { ok: false, reason: oneLine(listening, 200) };
  const address = server.address();
  if (address === null || typeof address === "string") {
    await new Promise((resolve) => server.close(() => resolve()));
    return { ok: false, reason: "the server bound no inspectable address" };
  }
  bound2 = { host: address.address, port: address.port };
  return {
    ok: true,
    server: {
      host: bound2.host,
      port: bound2.port,
      url: `http://${bound2.host}:${bound2.port}`,
      close: () => new Promise((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      })
    }
  };
}

// packages/cli/src/commands/runs.ts
var USAGE6 = [
  "Usage: delivery-harness runs capabilities --json",
  "       delivery-harness runs list [--json] [--limit <n>] [--status <status>] [--open|--ended]",
  "       delivery-harness runs show [<run-id>] [--json]",
  "       delivery-harness runs grammar <kind> [--version 1|2] [--json]",
  "       delivery-harness runs view <run-id> [--json] [--record <repository-relative-path>]",
  "       delivery-harness runs export <run-id> --output <file>",
  "       delivery-harness runs archive <file> [--artifact <id>]",
  "       delivery-harness runs capture <run-id> --json <request>",
  "       delivery-harness runs artifact <run-id> <artifact-id> [--json]",
  "       delivery-harness runs serve [--repo <path>]... [--archive <file>]... [--port <n>] [--freshness-seconds <n>] [--record <repository-relative-path>]"
].join("\n");
var unresolvable = (reason) => ({
  kind: "blocked",
  blockers: [
    runSurfaceBlocker({
      code: "run_store_unresolvable",
      summary: "The run store could not be resolved.",
      details: oneLine(reason, 200),
      remediation: {
        id: "run-inside-a-repository",
        summary: "Run this command inside a git repository; the run store lives under its common directory."
      }
    })
  ]
});
var runsCommand = {
  name: "runs",
  sourceId: "delivery-harness.cli.runs",
  summary: "List, show, and serve the delivery runs this repository has recorded.",
  usage: USAGE6,
  configFree: true,
  async run(context) {
    const [subcommand, ...rest] = context.args;
    if (subcommand === "capabilities") {
      if (rest.length !== 1 || rest[0] !== "--json") return { kind: "usage", message: "Usage: runs capabilities --json" };
      context.write(`${JSON.stringify({ spec: "run-capabilities/1", writerVersions: ["run-event/1", "run-event/2"], artifactCapture: true })}
`);
      return { kind: "ok" };
    }
    if (subcommand === "grammar") return showGrammar(context, rest);
    if (subcommand === "view") return runViewCommand(context, rest);
    if (subcommand === "export" || subcommand === "archive") return runArchiveCommand(context, subcommand, rest);
    if (subcommand === "capture" || subcommand === "artifact") return runArtifactCommand(context, subcommand, rest);
    if (subcommand === void 0) return { kind: "usage", message: `runs needs a subcommand.
${USAGE6}` };
    if (subcommand !== "list" && subcommand !== "show" && subcommand !== "serve") {
      return { kind: "usage", message: `Unknown runs subcommand ${oneLine(subcommand, 64)}.
${USAGE6}` };
    }
    if (subcommand === "serve") return serveRuns(context, rest);
    const listArgs = subcommand === "list" ? parseListArgs(rest) : void 0;
    if (listArgs !== void 0 && !listArgs.ok) return { kind: "usage", message: listArgs.message };
    const showArgs = subcommand === "show" ? parseShowArgs(rest) : void 0;
    if (showArgs !== void 0 && !showArgs.ok) return { kind: "usage", message: showArgs.message };
    const resolved = await resolveRunSurface(context.rootDir);
    if (!resolved.ok) return unresolvable(resolved.reason);
    if (listArgs !== void 0 && listArgs.ok) return listRuns(resolved.surface, context, listArgs.args);
    if (showArgs === void 0 || !showArgs.ok) throw new Error("runs show arguments were not parsed");
    let runId = showArgs.args.runId;
    if (runId === void 0) {
      const current = await resolved.surface.store.current(resolved.surface.worktreeKey);
      if (!current.ok || current.runId === void 0) {
        return {
          kind: "blocked",
          blockers: [runSurfaceBlocker({
            code: "run_unresolvable",
            summary: "There is no current run that can be resolved in this worktree.",
            details: current.ok ? "no run id was supplied and the worktree has no readable current run" : `no run id was supplied; current-run lookup was refused: ${oneLine(current.rejections[0]?.message ?? "unreadable", 200)}`,
            remediation: {
              id: "start-or-name-a-run",
              summary: "Start a run with `delivery-harness emit run.started`, or pass a run id from `delivery-harness runs list`."
            }
          })]
        };
      }
      runId = current.runId;
    }
    return showRun(resolved.surface, context, runId, showArgs.args.json);
  }
};
function parseShowArgs(args) {
  let runId;
  let json = false;
  for (const token of args) {
    if (token === "--json") {
      if (json) return { ok: false, message: `runs show: --json was given twice.
${USAGE6}` };
      json = true;
      continue;
    }
    if (token.startsWith("-")) return { ok: false, message: `runs show: unknown flag ${oneLine(token, 64)}.
${USAGE6}` };
    if (runId !== void 0) return { ok: false, message: `runs show accepts at most one run id.
${USAGE6}` };
    runId = token;
  }
  return { ok: true, args: { json, ...runId === void 0 ? {} : { runId } } };
}
function parseGrammarArgs(args) {
  let kind;
  let version = RUN_EVENT_SPEC_V2;
  let versionSeen = false;
  let json = false;
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--json") {
      if (json) return { ok: false, message: `runs grammar: --json was given twice.
${USAGE6}` };
      json = true;
      continue;
    }
    if (token === "--version") {
      if (versionSeen) return { ok: false, message: `runs grammar: --version was given twice.
${USAGE6}` };
      const value2 = args[index + 1];
      if (value2 !== "1" && value2 !== "2") return { ok: false, message: `runs grammar: --version needs 1 or 2.
${USAGE6}` };
      version = value2 === "1" ? RUN_EVENT_SPEC : RUN_EVENT_SPEC_V2;
      versionSeen = true;
      index += 1;
      continue;
    }
    if (token.startsWith("-")) return { ok: false, message: `runs grammar: unknown flag ${oneLine(token, 64)}.
${USAGE6}` };
    if (kind !== void 0) return { ok: false, message: `runs grammar accepts exactly one event kind.
${USAGE6}` };
    kind = token;
  }
  if (kind === void 0) return { ok: false, message: `runs grammar needs an event kind.
${USAGE6}` };
  return { ok: true, args: { kind, version, json } };
}
function renderValue(value2, indent, lead, context) {
  context.write(`${indent}${lead}${value2.type}  ${value2.constraint}${value2.values === void 0 ? "" : `  values: ${value2.values.join(", ")}`}`);
  context.write(`${indent}  example: ${JSON.stringify(value2.example)}`);
  for (const member of value2.members ?? []) renderMember(member, `${indent}  `, context);
  if (value2.items !== void 0) renderValue(value2.items, `${indent}  `, "items  ", context);
  for (const [index, variant] of (value2.variants ?? []).entries()) {
    renderValue(variant, `${indent}  `, `variant ${index + 1}  `, context);
  }
}
function renderMember(member, indent, context) {
  renderValue(member, indent, `${member.name}  ${member.required ? "required" : "optional"}  `, context);
}
function renderGrammar(grammar, context) {
  context.write(`${grammar.kind} (${grammar.version})`);
  for (const member of grammar.members) renderMember(member, "  ", context);
  context.write("  example:");
  context.write(JSON.stringify(grammar.example, null, 2));
}
function showGrammar(context, args) {
  const parsed = parseGrammarArgs(args);
  if (!parsed.ok) return { kind: "usage", message: parsed.message };
  const grammar = describeRunEventPayload(parsed.args.kind, parsed.args.version);
  if (grammar === void 0) {
    const kinds = parsed.args.version === RUN_EVENT_SPEC ? RUN_EVENT_KINDS_V1 : RUN_EVENT_KINDS;
    return {
      kind: "usage",
      message: `runs grammar: ${oneLine(parsed.args.kind, 128)} is not a ${parsed.args.version} event kind; accepted kinds: ${kinds.join(", ")}.
${USAGE6}`
    };
  }
  if (parsed.args.json) context.write(JSON.stringify(grammar, null, 2));
  else renderGrammar(grammar, context);
  return { kind: "ok" };
}
var RUN_LIST_UNREADABLE = "unreadable";
var STATUS_NEVER_LISTED = "absent";
var RUN_LIST_STATUSES = Object.freeze([
  ...RUN_JOURNAL_STATUSES.filter((status) => status !== STATUS_NEVER_LISTED),
  RUN_LIST_UNREADABLE
]);
var RUN_INVENTORY_SPEC = "run-inventory/1";
function parseListArgs(args) {
  const refuse = (reason) => ({ ok: false, message: `runs list: ${reason}.
${USAGE6}` });
  let json = false;
  let limit;
  let status;
  let open3;
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--json") {
      if (json) return refuse("--json was given twice");
      json = true;
      continue;
    }
    if (token === "--open" || token === "--ended") {
      if (open3 !== void 0) return refuse("use at most one of --open and --ended");
      open3 = token === "--open";
      continue;
    }
    if (token === "--limit" || token === "--status") {
      const value2 = args[index + 1];
      if (value2 === void 0) return refuse(`${token} needs a value`);
      index += 1;
      if (token === "--limit") {
        if (limit !== void 0) return refuse("--limit was given twice");
        if (!/^\d+$/.test(value2) || !Number.isSafeInteger(Number(value2)) || Number(value2) < 1) {
          return refuse(`--limit needs a positive whole number, not ${oneLine(value2, 64)}`);
        }
        limit = Number(value2);
        continue;
      }
      if (status !== void 0) return refuse("--status was given twice");
      if (!RUN_LIST_STATUSES.includes(value2)) {
        return refuse(`--status accepts ${RUN_LIST_STATUSES.join(", ")}, not ${oneLine(value2, 64)}`);
      }
      status = value2;
      continue;
    }
    if (token.startsWith("--")) return refuse(`unknown flag ${oneLine(token, 64)}`);
    return refuse("it takes no positional arguments");
  }
  return {
    ok: true,
    args: { json, ...limit === void 0 ? {} : { limit }, ...status === void 0 ? {} : { status }, ...open3 === void 0 ? {} : { open: open3 } }
  };
}
async function sizeOf(runsDir, runId) {
  try {
    return (await stat3(path20.join(runsDir, `${runId}.jsonl`))).size;
  } catch {
    return 0;
  }
}
async function inventoryOf(surface) {
  const runIds = await surface.store.list();
  const current = await surface.store.current(surface.worktreeKey);
  const currentRunId = current.ok ? current.runId : void 0;
  const rows = [];
  for (const runId of runIds) {
    const bytes = await sizeOf(surface.runsDir, runId);
    const read = await surface.store.read(runId);
    rows.push(
      read.ok ? {
        runId,
        status: evaluateRunJournal4(read.events).status,
        open: !read.events.some((event) => event.kind === "run.ended"),
        current: runId === currentRunId,
        durationSeconds: summarize(read.events).durationSeconds,
        bytes
      } : (
        // Nothing was read, so no lifecycle or completeness state is claimed.
        // The worktree pointer and on-disk size are independent readable facts.
        { runId, status: RUN_LIST_UNREADABLE, open: false, current: runId === currentRunId, durationSeconds: null, bytes }
      )
    );
  }
  return { rows, currentRunId };
}
async function listRuns(surface, context, args) {
  const { rows, currentRunId } = await inventoryOf(surface);
  const selected = rows.filter(
    (row) => (args.status === void 0 || row.status === args.status) && (args.open === void 0 || row.status !== RUN_LIST_UNREADABLE && row.open === args.open)
  );
  const totalBytes = selected.reduce((sum, row) => sum + row.bytes, 0);
  const shown = args.limit === void 0 ? selected : selected.slice(0, args.limit);
  const truncated = shown.length < selected.length;
  if (args.json) {
    context.write(
      JSON.stringify(
        {
          spec: RUN_INVENTORY_SPEC,
          labels: READOUT_LABELS,
          runsDir: surface.runsDir,
          current: currentRunId ?? null,
          runs: shown,
          total: { count: selected.length, bytes: totalBytes },
          returned: shown.length,
          truncated
        },
        null,
        2
      )
    );
    return { kind: "ok" };
  }
  const lines = [`runs in ${oneLine(surface.runsDir, 400)}`, `  (${READOUT_LABELS})`];
  for (const row of shown) {
    lines.push(
      row.status === RUN_LIST_UNREADABLE || row.durationSeconds === null ? `  ${row.runId}  ${RUN_LIST_UNREADABLE}  ${row.bytes} bytes` : `  ${row.runId}  ${row.status}  ${row.open ? "open" : "ended"}${row.current ? " current" : ""}  ${durationLabel(row.durationSeconds)}  ${row.bytes} bytes`
    );
  }
  lines.push(`total ${totalBytes} bytes across ${selected.length} run(s)`);
  if (truncated) lines.push(`showing ${shown.length} of ${selected.length} run(s) (--limit ${args.limit})`);
  for (const line of lines) context.write(line);
  return { kind: "ok" };
}
async function showRun(surface, context, runId, json) {
  const read = await surface.store.read(runId);
  if (!read.ok) {
    return {
      kind: "blocked",
      blockers: [
        runSurfaceBlocker({
          code: "run_unresolvable",
          summary: "That run has no readable journal in this store.",
          details: `run ${oneLine(runId, 128)}: ${oneLine(read.rejections[0]?.message ?? "unreadable", 200)}`,
          remediation: { id: "list-the-runs", summary: "Run `delivery-harness runs list` to see the runs this repository holds." }
        })
      ]
    };
  }
  const events = read.events;
  if (json) {
    const worktreeRoot2 = await resolveWorktreeRoot(context.rootDir);
    const rootDir2 = worktreeRoot2.ok ? worktreeRoot2.root : context.rootDir;
    context.write(JSON.stringify(buildRunExport({
      runId,
      events,
      rootDir: rootDir2,
      refusedAppends: await surface.store.readNotes(runId)
    }), null, 2));
    return { kind: "ok" };
  }
  const open3 = !events.some((event) => event.kind === "run.ended");
  const current = await surface.store.current(surface.worktreeKey);
  const isCurrent = current.ok && current.runId === runId;
  context.write(`run ${runId}  ${open3 ? "open" : "ended"}${isCurrent ? "  current in this worktree" : ""}`);
  context.write("  events:");
  for (const event of events) {
    context.write(`    ${event.seq}  ${event.at}  ${event.kind.padEnd(20)}  ${event.actor.role.padEnd(8)}  ${detailOf(event)}`);
  }
  for (const row of phaseRows(events)) context.write(row);
  const rounds = roundRows(events);
  if (rounds.length > 0) {
    context.write("  rounds:");
    for (const row of rounds) context.write(`  ${row}`);
  }
  const decisions = events.filter((event) => event.kind === "decision.recorded");
  if (decisions.length > 0) {
    context.write("  decisions:");
    for (const decision of decisions) context.write(`    ${detailOf(decision)}`);
  }
  const notes = await surface.store.readNotes(runId);
  if (notes.length > 0) {
    context.write("  refused appends:");
    for (const entry of notes) {
      const note = typeof entry === "object" && entry !== null ? entry : {};
      context.write(
        `    ${oneLineOf(note["at"], 32)}  ${oneLineOf(note["kind"], 128)}  ${oneLineOf(note["code"], 64)}${note["pattern"] === void 0 ? "" : `  ${oneLineOf(note["pattern"], 64)}`}`
      );
    }
  }
  const worktreeRoot = await resolveWorktreeRoot(context.rootDir);
  const rootDir = worktreeRoot.ok ? worktreeRoot.root : context.rootDir;
  for (const row of readoutRows(events, evaluateRunJournal4(events), rootDir)) context.write(row);
  return { kind: "ok" };
}
var BROWSER_ELIDED_PORTS = [80, 443];
function parseServeArgs(args, rootDir) {
  const repos = [];
  const archives = [];
  let recordPath;
  let freshnessWindowMs;
  let port;
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--repo" || token === "--port" || token === "--archive" || token === "--freshness-seconds" || token === "--record") {
      const value2 = args[index + 1];
      if (value2 === void 0) return { ok: false, message: `${token} needs a value.
${USAGE6}` };
      index += 1;
      if (token === "--record") {
        if (recordPath !== void 0) return { ok: false, message: "Use one explicit record path" };
        recordPath = value2;
        continue;
      }
      if (token === "--archive") {
        archives.push(path20.resolve(rootDir, value2));
        continue;
      }
      if (token === "--freshness-seconds") {
        if (!/^\d+$/.test(value2) || !Number.isSafeInteger(Number(value2)) || Number(value2) > 86400) return { ok: false, message: "freshness seconds must be between 0 and 86400" };
        freshnessWindowMs = Number(value2) * 1e3;
        continue;
      }
      if (token === "--repo") {
        repos.push(path20.resolve(rootDir, value2));
        continue;
      }
      if (!/^\d{1,5}$/.test(value2)) return { ok: false, message: `--port needs a port number.
${USAGE6}` };
      const parsed = Number(value2);
      if (parsed > 65535) return { ok: false, message: `--port needs a port number.
${USAGE6}` };
      if (BROWSER_ELIDED_PORTS.includes(parsed)) {
        return {
          ok: false,
          message: `--port ${parsed} cannot be served: a browser omits a scheme's default port from the Host header, and this page answers only to the exact host:port it bound.
${USAGE6}`
        };
      }
      port = parsed;
      continue;
    }
    if (token.startsWith("--")) return { ok: false, message: `Unknown flag ${oneLine(token, 64)}.
${USAGE6}` };
    return { ok: false, message: `runs serve takes no positional arguments.
${USAGE6}` };
  }
  return { ok: true, args: { repos: repos.length === 0 && archives.length === 0 ? [rootDir] : repos, archives, ...recordPath === void 0 ? {} : { recordPath }, ...freshnessWindowMs === void 0 ? {} : { freshnessWindowMs }, ...port === void 0 ? {} : { port } } };
}
async function serveRuns(context, args) {
  const parsed = parseServeArgs(args, context.rootDir);
  if (!parsed.ok) return { kind: "usage", message: parsed.message };
  let archives;
  try {
    archives = await Promise.all(parsed.args.archives.map(async (file) => ({ label: path20.basename(file), text: await readArchiveFile(file) })));
  } catch {
    return unresolvable("An explicitly selected archive could not be read within its size limit.");
  }
  const started = await startRunServer({ repos: parsed.args.repos, archives, ...parsed.args.recordPath === void 0 ? {} : { recordPath: parsed.args.recordPath }, ...parsed.args.freshnessWindowMs === void 0 ? {} : { freshnessWindowMs: parsed.args.freshnessWindowMs }, ...parsed.args.port === void 0 ? {} : { port: parsed.args.port } });
  if (!started.ok) return unresolvable(started.reason);
  const server = started.server;
  context.write(`serving ${parsed.args.repos.length} repository path(s) at ${server.url}`);
  context.write(`  (${READOUT_LABELS})`);
  try {
    await untilSignalled(context.signal);
  } finally {
    await server.close();
  }
  return { kind: "ok" };
}
function untilSignalled(signal) {
  if (signal === void 0) return new Promise(() => {
  });
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
}

// packages/cli/src/commands/verify.ts
import { readFile as readFile9 } from "node:fs/promises";
import path21 from "node:path";
import {
  MAX_RUN_PROVIDER_ID,
  RUN_PROVIDER_ID,
  deliveryRecordPathFor as deliveryRecordPathFor4,
  needsCommittedSymlinkTarget,
  parseCandidateTreeListing,
  parseDeliveryRecord as parseDeliveryRecord2,
  runGitCommand as runGitCommand2,
  verifyDeliveryRecord as verifyDeliveryRecord2,
  capturePortableVerificationInputs as capturePortableVerificationInputs2,
  collectLiveProviderResults
} from "./kernel.mjs";
var USAGE7 = "Usage: delivery-harness verify [--require-run-journal] [--mandated-lens <id>]...";
function parseArgs2(args) {
  let requireRunJournal = false;
  const mandatedLensIds = [];
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--require-run-journal") {
      requireRunJournal = true;
      continue;
    }
    if (token === "--mandated-lens") {
      const value2 = args[index + 1];
      if (value2 === void 0) return { ok: false, message: `${token} needs a value.
${USAGE7}` };
      if (value2.length > MAX_RUN_PROVIDER_ID || !RUN_PROVIDER_ID.test(value2)) {
        return { ok: false, message: `${token} takes a bounded lens id, not ${oneLine(value2, 64)}.
${USAGE7}` };
      }
      mandatedLensIds.push(value2);
      index += 1;
      continue;
    }
    if (token.startsWith("-")) return { ok: false, message: `Unknown flag ${oneLine(token, 64)}.
${USAGE7}` };
    return { ok: false, message: `verify takes no positional arguments, and ${oneLine(token, 64)} is one.
${USAGE7}` };
  }
  return { ok: true, args: { requireRunJournal, mandatedLensIds } };
}
function runJournalBlocker(row) {
  const missing = row.missing.length === 0 ? "(none)" : row.missing.join(", ");
  const violations = row.violations === void 0 || row.violations.length === 0 ? "(none)" : row.violations.join(", ");
  const why = (row.explanations ?? []).map(
    (explanation) => `; ${oneLine(explanation.violation, 64)}${explanation.consequenceOf === void 0 ? "" : ` (a consequence of ${oneLine(explanation.consequenceOf, 64)})`}: ${oneLine(explanation.because, 120)}`
  ).join("");
  return commandBlocker({
    code: "run_journal_incomplete",
    sourceId: "delivery-harness.cli.verify",
    summary: "The run journal for this candidate is not complete, and --require-run-journal was given.",
    details: `status ${row.status}${row.runId === void 0 ? "" : ` (run ${oneLine(row.runId, 128)})`}; violations: ${violations}; missing: ${missing}; ${RUN_JOURNAL_ADMISSION_ROW}${why}`,
    remediations: [
      {
        id: "emit-the-missing-run-events",
        kind: "manual_action",
        summary: "Emit the run events this delivery did not journal, or drop --require-run-journal: the row is observability, not evidence."
      }
    ]
  });
}
async function runSpanRows(rootDir, record4) {
  const span = record4.runSpan;
  if (span === void 0) return { rows: [] };
  const spent = (Date.parse(span.endedAt) - Date.parse(span.startedAt)) / 1e3;
  const recorded = `recorded run span: ${span.startedAt} to ${span.endedAt} (${durationLabel(spent)})`;
  const journal = await resolveRunSpan({
    cwd: rootDir,
    treeSha: record4.candidateBinding.treeSha,
    preferStartedAt: span.startedAt
  });
  if (journal === void 0) {
    return { rows: [`${recorded}; unchecked: no run journal in this repository binds this candidate`] };
  }
  const disagreement = span.startedAt !== journal.startedAt ? `the record starts at ${span.startedAt} but run ${oneLine(journal.runId, 128)} starts at ${journal.startedAt}` : span.endedAt > journal.endedAt ? `the record ends at ${span.endedAt}, after the last instant run ${oneLine(journal.runId, 128)} reached (${journal.endedAt})` : void 0;
  if (disagreement === void 0) {
    return { rows: [`${recorded}; checked against run ${oneLine(journal.runId, 128)}`] };
  }
  return {
    rows: [],
    blocker: commandBlocker({
      code: "record_run_span_mismatch",
      sourceId: "delivery-harness.cli.verify",
      summary: "The delivery record's run span disagrees with the run journal that binds its candidate.",
      details: disagreement,
      remediations: [
        {
          id: "re-record-the-span",
          kind: "command",
          command: ["delivery-harness", "record"],
          summary: "Re-record this candidate so its span is the one its own run journal reports."
        }
      ]
    })
  };
}
var verifyCommand = {
  capturesCandidate: true,
  name: "verify",
  sourceId: "delivery-harness.cli.verify",
  summary: "Verify the tracked delivery record against the current candidate.",
  usage: USAGE7,
  async run(context) {
    const observedAt = `${(/* @__PURE__ */ new Date()).toISOString().slice(0, 19)}Z`;
    const parsedArgs = parseArgs2(context.args);
    if (!parsedArgs.ok) return { kind: "usage", message: parsedArgs.message };
    const focused = await focusedOutcome(context);
    if (focused !== void 0) return focused;
    const wiring = await context.wire();
    const capture2 = await wiring.captureCandidate();
    if (!capture2.ok) {
      return { kind: "blocked", blockers: [...capture2.blockers] };
    }
    const identity = {
      deliverableDigest: capture2.candidate.deliverable.digest,
      identityToken: capture2.candidate.deliverable.identity
    };
    const base = {
      ref: capture2.candidate.base.ref,
      tipSha: capture2.candidate.base.tipSha,
      mergeBaseSha: capture2.candidate.base.mergeBaseSha
    };
    const relativePath = deliveryRecordPathFor4(context.config, identity.deliverableDigest);
    const absolutePath = path21.join(context.rootDir, relativePath);
    let text2;
    try {
      text2 = await readFile9(absolutePath, "utf8");
    } catch {
      return {
        kind: "blocked",
        blockers: [
          commandBlocker({
            code: "delivery_record_missing",
            sourceId: "delivery-harness.cli.verify",
            summary: "No delivery record describes the current candidate.",
            details: `expected ${relativePath}`,
            remediations: [
              {
                id: "run-record",
                kind: "command",
                command: ["delivery-harness", "record"],
                summary: "Record the admitted gate for this candidate."
              }
            ]
          })
        ]
      };
    }
    const parsed = parseDeliveryRecord2(text2);
    if (!parsed.ok) {
      return { kind: "blocked", blockers: [...parsed.blockers] };
    }
    const listing = await runGitCommand2(["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"], {
      cwd: context.rootDir
    });
    const candidateTreePaths = [];
    if (listing.exitCode === 0) {
      for (const entry of parseCandidateTreeListing(listing.stdout)) {
        if (!needsCommittedSymlinkTarget(entry)) {
          candidateTreePaths.push(entry);
          continue;
        }
        const blob = await runGitCommand2(["git", "cat-file", "blob", entry.objectSha], { cwd: context.rootDir });
        candidateTreePaths.push(blob.exitCode === 0 ? { ...entry, symlinkTarget: blob.stdout } : entry);
      }
    }
    const inputs = await capturePortableVerificationInputs2(context.rootDir, context.config, capture2.candidate, parsed.record);
    const live = await collectLiveProviderResults({
      rootDir: context.rootDir,
      config: context.config,
      candidate: capture2.candidate,
      projection: inputs.projection,
      evidenceContext: inputs.evidenceContext,
      env: context.env,
      ...context.signal === void 0 ? {} : { signal: context.signal }
    });
    const verified = verifyDeliveryRecord2(
      context.config,
      parsed.record,
      identity,
      base,
      { candidateTreePaths, ...inputs, observedAt, liveResults: live.liveResults, executionContext: context.classifyContext() }
    );
    if (!verified.ok) {
      return { kind: "blocked", blockers: [...live.blockers, ...verified.blockers] };
    }
    const runJournal = await resolveRunJournalRow({
      cwd: context.rootDir,
      treeSha: parsed.record.candidateBinding.treeSha,
      reviewedCandidateTreeShas: verified.reviewedCandidateTreeShas,
      ...parsedArgs.args.mandatedLensIds.length === 0 ? {} : { mandatedLensIds: parsedArgs.args.mandatedLensIds }
    });
    const check = verifyDeliveryRecord2(
      context.config,
      parsed.record,
      identity,
      base,
      { candidateTreePaths, runJournal, ...inputs, observedAt, liveResults: live.liveResults, executionContext: context.classifyContext() }
    );
    if (parsedArgs.args.requireRunJournal && runJournal.status !== "complete") {
      return { kind: "blocked", blockers: [runJournalBlocker(runJournal)] };
    }
    const span = await runSpanRows(context.rootDir, parsed.record);
    if (span.blocker !== void 0) return { kind: "blocked", blockers: [span.blocker] };
    const relaxation = check.baseMovementRelaxed ? ` (base movement relaxed by policy: ${check.relaxedDriftClasses.join(", ")})` : "";
    const exemption = check.hostedChecks.exemption;
    const hostedCheckRow = exemption === void 0 ? [] : [
      `hosted checks: exempted for ${oneLine(exemption.scope.repositoryId, 128)} at ${oneLine(exemption.scope.baseRef, 256)}; granted by ${oneLine(exemption.grantedBy, 256)}; until ${exemption.until}; reason: ${oneLine(exemption.reason, 512)}`
    ];
    return {
      kind: "ok",
      summary: [
        `verified ${relativePath}${relaxation}; attestation: ${check.attestationLabel}`,
        `recorded base: ${oneLine(parsed.record.candidateBinding.baseRef, 256)} at ${parsed.record.candidateBinding.baseTipSha}`,
        `observed base: ${oneLine(base.ref, 256)} at ${base.tipSha}`,
        ...span.rows,
        ...hostedCheckRow,
        ...runJournalRows(runJournal)
      ].join("\n")
    };
  }
};

// packages/cli/src/commands/save-context.ts
import { describeRunEventPayload as describeRunEventPayload2, RUN_EVENT_SPEC as RUN_EVENT_SPEC2 } from "./kernel.mjs";

// packages/cli/src/ordinary-context.ts
import { readFile as readFile10 } from "node:fs/promises";
import path22 from "node:path";
import { HARNESS_VERSION, digestCanonical as digestCanonical9 } from "./kernel.mjs";
import { reconciliationActions } from "./kernel.mjs";
function recoveryBlocker(code, summary, details) {
  return commandBlocker({
    code,
    sourceId: "delivery-harness.cli.resume",
    summary,
    ...details === void 0 ? {} : { details },
    remediations: [{ id: "reconcile-delivery-context", kind: "manual_action", summary: "Inspect the saved context and actual workspace/external state with host tools; rerun preparation and gates where evidence is not current. Never replay an uncertain action automatically." }]
  });
}
function rejectionDetails(rejections) {
  const first = rejections[0];
  if (first === void 0) return "the store refused the append";
  return `${oneLine(first.code, 64)} at ${oneLine(first.pointer, 128) || "/"}: ${oneLine(first.message, 200)}`;
}
function ordinaryEventWriter(version, kind, payload) {
  if (version !== "run-event/2") return { version };
  return { version, eventId: `${kind.replaceAll(".", "-")}-${digestCanonical9(payload)}` };
}
async function recoveryRun(rootDir, named) {
  const resolved = await resolveRunSurface(rootDir);
  if (!resolved.ok) return { ok: false, blocker: recoveryBlocker("resume_context_invalid", "The repository run store cannot be read.") };
  const current = await resolved.surface.store.current(resolved.surface.worktreeKey);
  const runId = named ?? (current.ok ? current.runId : void 0);
  if (!runId) return { ok: false, blocker: recoveryBlocker("resume_context_missing", "No current delivery run was found; start a run or name its existing id with --run.") };
  const read = await resolved.surface.store.read(runId);
  if (!read.ok) return { ok: false, blocker: recoveryBlocker("resume_context_invalid", "The delivery journal is missing, corrupt, or inaccessible.", rejectionDetails(read.rejections)) };
  return { ok: true, surface: resolved.surface, runId, events: read.events, version: read.events[0]?.version ?? "run-event/1" };
}
async function installedRelease(rootDir) {
  const document = JSON.parse(await readFile10(path22.join(rootDir, ".agent-skills/active.json"), "utf8"));
  const release = document?.release;
  if (!release || typeof release.releaseId !== "string" || typeof release.profile !== "string" || typeof release.archiveSha256 !== "string" || !/^[0-9a-f]{64}$/.test(release.archiveSha256)) {
    throw new Error("Installed workflow release identity is missing or malformed.");
  }
  return { runtimeVersion: HARNESS_VERSION, releaseId: release.releaseId, profile: release.profile, archiveSha256: release.archiveSha256 };
}
var policyDigest = (context) => digestCanonical9({ config: context.config, policyBinding: context.policyBinding ?? null });

// packages/cli/src/commands/save-context.ts
var USAGE8 = `Usage: delivery-harness save-context --json '{"contract":{"objective":"...","acceptanceCriteria":["..."],"finishLine":"merge-ready"},"stage":"work"}'`;
var members = describeRunEventPayload2("context.saved", RUN_EVENT_SPEC2).members;
var bound = (name, member) => `  ${name}: ${member.constraint}${member.items ? `, each ${member.items.constraint}` : ""}`;
var BOUNDS = [
  "Bounds:",
  ...members.find((m) => m.name === "contract").members.map((m) => bound(`contract.${m.name}`, m)),
  bound("stage", members.find((m) => m.name === "stage"))
].join("\n");
var saveContextCommand = {
  name: "save-context",
  sourceId: "delivery-harness.cli.save-context",
  summary: "Save a bounded delivery contract and stage observation in the current run.",
  usage: `${USAGE8}
${BOUNDS}`,
  async run(context) {
    if (context.args.length !== 2 || context.args[0] !== "--json") return { kind: "usage", message: USAGE8 };
    let input;
    try {
      const value2 = JSON.parse(context.args[1]);
      if (!value2 || typeof value2 !== "object" || Array.isArray(value2) || Object.keys(value2).some((key) => key !== "contract" && key !== "stage")) throw new Error();
      input = value2;
    } catch {
      return { kind: "usage", message: "Context must be a JSON object with only contract and stage." };
    }
    const run = await recoveryRun(context.rootDir);
    if (!run.ok) return { kind: "blocked", blockers: [run.blocker] };
    const wiring = await context.wire();
    const capture2 = await wiring.captureCandidate();
    if (!capture2.ok) return { kind: "blocked", blockers: capture2.blockers };
    let release;
    try {
      release = await installedRelease(context.rootDir);
    } catch {
      return { kind: "blocked", blockers: [recoveryBlocker("resume_release_unreadable", "The installed workflow release identity cannot be read; restore a valid installation before saving context.")] };
    }
    const candidate = capture2.candidate;
    const payload = {
      spec: "ordinary-run-context/1",
      ...input.contract === void 0 ? {} : { contract: input.contract },
      ...input.stage === void 0 ? {} : { stage: input.stage },
      candidateTreeSha: candidate.treeSha,
      candidateBinding: {
        deliverableDigest: candidate.deliverable.digest,
        identity: candidate.deliverable.identity,
        baseRef: candidate.base.ref,
        baseTipSha: candidate.base.tipSha,
        mergeBaseSha: candidate.base.mergeBaseSha,
        workspaceId: candidate.workspaceId
      },
      policyDigest: policyDigest(context),
      release
    };
    const writer = ordinaryEventWriter(run.version, "context.saved", payload);
    const appended = await run.surface.store.append(
      run.runId,
      buildRunEvent({ runId: run.runId, commonDir: run.surface.commonDir, kind: "context.saved", role: "executor", payload, ...writer }),
      // An exact retry of one save is one observation, at its first instant.
      { reuseExistingTimestamp: true }
    );
    if (!appended.ok) return { kind: "blocked", blockers: [recoveryBlocker(
      "resume_context_invalid",
      "Context was refused by the bounded run-event contract; inspect contract, stage, and secret-free inputs. `save-context --help` lists every bound.",
      `run ${run.runId}: ${rejectionDetails(appended.rejections)}`
    )] };
    return { kind: "ok", summary: `saved ordinary context for ${run.runId}; stage is an observation, not evidence` };
  }
};

// packages/cli/src/commands/resume.ts
import { classifyCandidateDrift as classifyCandidateDrift3, digestCanonical as digestCanonical10, evaluatePreparationReceipt as evaluatePreparationReceipt4, runAdmission as runAdmission2 } from "./kernel.mjs";
var USAGE9 = "Usage: delivery-harness resume [--run <run-id>]";
var resumeCommand = {
  name: "resume",
  sourceId: "delivery-harness.cli.resume",
  summary: "Read saved ordinary context and recheck evidence without executing or replaying work.",
  usage: USAGE9,
  async run(context) {
    if (context.args.length !== 0 && (context.args.length !== 2 || context.args[0] !== "--run")) return { kind: "usage", message: USAGE9 };
    const focused = await focusedOutcome(context);
    if (focused !== void 0) return focused;
    const run = await recoveryRun(context.rootDir, context.args[1]);
    if (!run.ok) return { kind: "blocked", blockers: [run.blocker] };
    const saved = [...run.events].reverse().find((event) => event.kind === "context.saved");
    if (!saved) return { kind: "blocked", blockers: [recoveryBlocker("resume_context_missing", "This run has no saved ordinary context. Recover the contract from its work item and save it explicitly.")] };
    const p = saved.payload;
    const actions = reconciliationActions(run.events);
    const blockers = actions.some((action) => action.outcome === "unknown" || action.inconsistent) ? [recoveryBlocker("resume_action_unreconciled", "An external action has no consistent observed outcome. Reconcile its reference with host tools before any repeat.")] : [];
    const wiring = await context.wire();
    const capture2 = await wiring.captureCandidate();
    const drift = [];
    if (p["policyDigest"] !== policyDigest(context)) drift.push("policy_changed");
    try {
      if (digestCanonical10(p["release"]) !== digestCanonical10(await installedRelease(context.rootDir))) drift.push("release_changed");
    } catch {
      drift.push("release_unreadable");
    }
    if (capture2.ok) {
      const binding = p["candidateBinding"];
      const expected = {
        treeSha: saved.candidateTreeSha,
        deliverable: { digest: binding["deliverableDigest"], identity: binding["identity"] },
        base: { ref: binding["baseRef"], tipSha: binding["baseTipSha"], mergeBaseSha: binding["mergeBaseSha"] },
        workspaceId: binding["workspaceId"]
      };
      drift.push(...classifyCandidateDrift3(expected, capture2.candidate));
      if (expected.deliverable.identity !== capture2.candidate.deliverable.identity) drift.push("identity_changed");
      if (expected.base.ref !== capture2.candidate.base.ref) drift.push("base_ref_changed");
    } else blockers.push(...capture2.blockers);
    const preparation = capture2.ok ? await evaluatePreparationReceipt4(context.rootDir, { config: context.config, candidate: capture2.candidate }, wiring.storageOptions) : void 0;
    const admission = capture2.ok ? await runAdmission2(
      { rootDir: context.rootDir, config: context.config, context: context.classifyContext() },
      { captureCandidate: wiring.captureCandidate, projectActivation: wiring.projectActivation, ...wiring.storageOptions }
    ) : void 0;
    if (capture2.ok && admission?.candidate && classifyCandidateDrift3(capture2.candidate, admission.candidate).length > 0) drift.push("candidate_observation_changed");
    try {
      if (digestCanonical10(p["release"]) !== digestCanonical10(await installedRelease(context.rootDir)) && !drift.includes("release_changed")) drift.push("release_changed");
    } catch {
      if (!drift.includes("release_unreadable")) drift.push("release_unreadable");
    }
    if (admission) blockers.push(...admission.blockers);
    if (drift.some((item2) => item2 !== "raw_tree_changed")) blockers.push(recoveryBlocker("resume_context_stale", "Saved candidate, policy, or release bindings changed; revalidate the contract and affected work before saving fresh context."));
    const reuseAllowed = blockers.length === 0 && admission?.admitted === true;
    context.write(JSON.stringify({
      spec: "ordinary-resume/1",
      runId: run.runId,
      observationOnly: true,
      automaticReplay: false,
      savedContext: p,
      actions,
      drift,
      preparation: preparation ?? { prepared: false },
      admission: admission ?? { admitted: false },
      reuseAllowed
    }));
    return blockers.length > 0 ? { kind: "blocked", blockers } : { kind: "ok" };
  }
};

// packages/cli/src/index.ts
var PACKAGE_NAME = "@agent-delivery-harness/cli";
var COMMANDS = [
  admitCommand,
  prepareCommand,
  reviewContextCommand,
  emitReviewEvidenceCommand,
  submitEvidenceCommand,
  gateCommand,
  recordCommand,
  verifyCommand,
  checkCommand,
  managedCommand,
  maintainCommand,
  emitCommand,
  runsCommand,
  saveContextCommand,
  resumeCommand
];
function runCli(argv, runtime) {
  return runCliBoundary(argv, COMMANDS, runtime);
}
export {
  COMMANDS,
  COMPLETION_WRAPPED_COMMANDS,
  CliInterruption,
  EXIT_INTERRUPTED,
  EXIT_OK,
  EXIT_POLICY,
  EXIT_USAGE,
  PACKAGE_NAME,
  admitCommand,
  buildRunExport,
  checkCommand,
  commandBlocker,
  emitCommand,
  emitReviewEvidenceCommand,
  gateCommand,
  importHarnessConfig,
  isConfigFreeCommand,
  maintainCommand,
  managedCommand,
  parseRunExport,
  prepareCommand,
  readScopedCheckDiagnostics,
  readScopedCheckObservations,
  recordCommand,
  resumeCommand,
  reviewContextCommand,
  runCli,
  runCliBoundary,
  runsCommand,
  saveContextCommand,
  submitEvidenceCommand,
  verifyCommand,
  wireRepo
};
