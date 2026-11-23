// Planted-failure servers: each is the neutral core, under the Cloudflare adapter, with exactly one rule removed,
// built at test time.
// Each must fail the conformance suite in exactly the scenarios it names, so a weakened suite turns the check red.
// A patch whose text is not found exactly as often as it says is drift, and the proof fails rather than passing.
import { build } from "esbuild";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export const SOURCE = fileURLToPath(new URL("../../src/", import.meta.url));

export const PLANTED = Object.freeze([
  {
    name: "errors-without-no-store",
    rule: "every response, errors included, carries Cache-Control: no-store",
    patches: [{
      file: "protocol.ts",
      find: "  return json({ error: error instanceof ApiError ? error.code : 'temporarily_unavailable' }, error instanceof ApiError ? error.status : 503);",
      replace: "  const { 'Cache-Control': _kept, ...rest } = headers;\n" +
        "  return Response.json({ error: error instanceof ApiError ? error.code : 'temporarily_unavailable' }, { status: error instanceof ApiError ? error.status : 503, headers: rest });",
    }],
    fails: ["security-headers"],
  },
  {
    name: "retry-refreshes-freshness",
    rule: "an exact retry returns its original receipt without refreshing source confirmation",
    patches: [{
      file: "core/deliveries.ts",
      find: "    if (retry) return retry;",
      replace: "    if (retry) { this.metadata.transact(publicId, tx => tx.confirm(this.clock.now(), tx.row()!.leaseUntil)); return retry; }",
    }],
    fails: ["exact-retry"],
  },
  {
    name: "no-writer-fencing",
    rule: "a commit or confirmation at an epoch the delivery is not at is fenced",
    patches: [{ file: "core/deliveries.ts", find: "    if (row.epoch !== epoch) fail('writer_fenced', 409);\n", replace: "" }],
    fails: ["writer-fencing"],
  },
  {
    name: "query-strings-accepted",
    rule: "a request with a query string is refused",
    patches: [{
      file: "core/service.ts",
      find: "if (url.search || url.hash || /[%\\\\]/.test(url.pathname))",
      replace: "if (url.hash || /[%\\\\]/.test(url.pathname))",
    }],
    fails: ["refused-requests"],
  },
  {
    name: "first-publisher-only",
    rule: "every repository in PUBLISHERS is honoured, not only the first",
    patches: [{
      file: "core/publishers.ts",
      find: "const value = Object.hasOwn(map, repository) ? map[repository] : undefined;",
      replace: "const value = Object.keys(map)[0] === repository ? map[repository] : undefined;",
    }],
    fails: ["round-trip.second-repository", "repository-scope", "anonymous-read"],
  },
  {
    name: "oversized-body-closes",
    rule: "an oversized body is answered with 413 body_too_large, not a closed connection",
    patches: [{
      file: "core/service.ts",
      find: "    } catch (error) { return errorResponse(error); }",
      replace: "    } catch (error) {\n" +
        "      if (error instanceof Error && error.message === 'body_too_large') return new Response(new ReadableStream({ start(c) { c.error(new Error('closed')); } }));\n" +
        "      return errorResponse(error);\n    }",
    }],
    fails: ["errors.413", "streamed-body-limit"],
  },
  {
    name: "streamed-bytes-uncounted",
    rule: "streamed bytes are counted against the limit, whatever Content-Length says",
    patches: [{ file: "protocol.ts", find: "      if (size > limit) { await reader.cancel(); fail('body_too_large', 413); }\n", replace: "" }],
    fails: ["streamed-body-limit"],
  },
  {
    name: "wrong-error-status",
    rule: "each fixed error code is sent with its own HTTP status",
    patches: [{ file: "core/service.ts", find: "fail('method_not_allowed', 405)", replace: "fail('method_not_allowed', 400)" }],
    fails: ["errors.405"],
  },
  {
    name: "unsupported-type-as-400",
    rule: "each refusal answers the status its code names, which the error rows check through their shared helper",
    patches: [{ file: "protocol.ts", find: "fail('unsupported_content_type', 415)", replace: "fail('unsupported_content_type', 400)" }],
    fails: ["errors.415"],
  },
  {
    name: "routes-by-host",
    rule: "routing and identity never read Host",
    patches: [
      { file: "core/service.ts", find: "deliveries(publicId)", replace: "deliveries(url.host + '/' + publicId)", count: 5 },
      { file: "core/service.ts", find: "deliveries(page[1])", replace: "deliveries(url.host + '/' + page[1])" },
    ],
    fails: ["host-headers-ignored"],
  },
]);

const occurrences = (text, find) => text.split(find).length - 1;

/** Copies the Worker's source, applies the patches and bundles it as the dry-run build does. Returns the module text. */
export async function buildWorker(patches = []) {
  const root = mkdtempSync(join(tmpdir(), "hosted-planted-"));
  try {
    const src = join(root, "src");
    cpSync(SOURCE, src, { recursive: true });
    for (const patch of patches) {
      const path = join(src, patch.file);
      const text = readFileSync(path, "utf8");
      const found = occurrences(text, patch.find);
      if (found !== (patch.count ?? 1))
        throw new Error(`planted patch drift: ${patch.file} holds its text ${found} times, not ${patch.count ?? 1}`);
      writeFileSync(path, text.split(patch.find).join(patch.replace));
    }
    const bundle = await build({
      entryPoints: [join(src, "cloudflare", "worker.ts")], bundle: true, write: false, format: "esm", platform: "neutral",
      target: "es2022", external: ["cloudflare:*", "node:*"], conditions: ["workerd", "worker", "browser"],
      mainFields: ["browser", "module", "main"], logLevel: "silent",
    });
    return bundle.outputFiles[0].text;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
