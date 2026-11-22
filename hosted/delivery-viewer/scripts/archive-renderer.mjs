// Freeze the trusted shell and content-addressed assets from an exact release build.
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

const [generatedModule, output] = process.argv.slice(2);
if (!generatedModule || !output) {
  throw Error("Usage: node archive-renderer.mjs <release src/generated-assets.ts> <archive.json.gz>");
}
const { assets, shell } = await import(pathToFileURL(resolve(generatedModule)));
const bytes = gzipSync(Buffer.from(JSON.stringify({ shell, assets })), { level: 9 });
await writeFile(output, bytes, { flag: "wx" });
console.log(createHash("sha256").update(bytes).digest("hex"));
