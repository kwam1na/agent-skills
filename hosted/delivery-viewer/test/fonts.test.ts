import { describe, it, expect } from "vitest";
import { cp, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { buildFonts, requestedFaces } from "../scripts/fonts.mjs";

const fonts = new URL("../fonts/", import.meta.url);
const render = await readFile(
  new URL("../../../skills/delivery-views/scripts/render.py", import.meta.url),
  "utf8",
);
const FONTS = JSON.parse(render.match(/^FONTS = ("[^"]*")$/m)![1]) as string;
const STYLESHEET = await readFile(
  new URL("../../../skills/delivery-views/assets/line-map.css", import.meta.url),
  "utf8",
);
type Face = { family: string; style: string; weight: number };
/** A copy of the fonts folder that a test may break. */
async function copy() {
  const dir = await mkdtemp(join(tmpdir(), "hosted-fonts-"));
  await cp(fonts, dir, { recursive: true });
  return { dir, url: pathToFileURL(dir + "/") };
}
function build(fontsDir: URL, options: { fontsUrl?: string; stylesheet?: string } = {}) {
  const served: string[] = [];
  return buildFonts({
    fontsDir,
    fontsUrl: options.fontsUrl ?? FONTS,
    stylesheet: options.stylesheet ?? STYLESHEET,
    asset: (_bytes: Uint8Array, extension: string) => {
      served.push(extension);
      return `/assets/${served.length}.${extension}`;
    },
  }).then((css: string) => ({ css, served }));
}

describe("the hosted page's fonts", () => {
  it("serves every face the local view requests from Google Fonts, from files it carries", async () => {
    const faces = requestedFaces(FONTS) as Face[];
    expect(faces).toEqual([
      { family: "Newsreader", style: "normal", weight: 300 },
      { family: "Newsreader", style: "normal", weight: 400 },
      { family: "Newsreader", style: "normal", weight: 500 },
      { family: "Newsreader", style: "italic", weight: 400 },
      ...[400, 500, 600].map((weight) => ({ family: "Geist", style: "normal", weight })),
      ...[400, 500, 600].map((weight) => ({ family: "Geist Mono", style: "normal", weight })),
    ]);
    const { css, served } = await build(fonts);
    for (const face of faces)
      expect(css).toContain(
        `font-family: "${face.family}"; font-style: ${face.style}; font-weight: ${face.weight}; font-display: swap; src: url(/assets/`,
      );
    expect(css).not.toMatch(/https?:/);
    expect(served.filter((kind) => kind === "txt")).toHaveLength(3);
    expect(css).toMatch(/SIL Open Font License 1\.1: Newsreader OFL-1\.1 \/assets\/\d+\.txt; Geist OFL-1\.1/);
  });
  it.each([
    ["missing", async (dir: string) => rm(join(dir, "geist/geist-latin-wght-normal.woff2"))],
    ["renamed", async (dir: string) => rename(join(dir, "geist-mono/geist-mono-latin-wght-normal.woff2"), join(dir, "geist-mono/geist-mono.woff2"))],
    ["licence missing", async (dir: string) => rm(join(dir, "newsreader/OFL.txt"))],
  ])("fails the build, naming the file, when a font file is %s", async (_case, breakIt) => {
    const { dir, url } = await copy();
    try {
      await breakIt(dir);
      await expect(build(url)).rejects.toThrow(/^hosted\.font_missing: (font|licence) [a-z-]+\/[A-Za-z-]+(\.woff2|\.txt) is listed/);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
  it("fails the build when a font file's bytes change from its recorded digest", async () => {
    const { dir, url } = await copy();
    try {
      await writeFile(join(dir, "geist/geist-latin-wght-normal.woff2"), "not the font");
      await expect(build(url)).rejects.toThrow("hosted.font_digest: font geist/geist-latin-wght-normal.woff2");
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
  it("fails the build when the stylesheet or the local view names a family or style it does not serve", async () => {
    await expect(
      build(fonts, { stylesheet: STYLESHEET.replace('--mono: "Geist Mono"', '--mono: "Fira Code"') }),
    ).rejects.toThrow("hosted.font_unserved: the stylesheet names Fira Code");
    await expect(
      build(fonts, { fontsUrl: FONTS.replace("family=Geist:wght@", "family=Geist:ital,wght@1,") }),
    ).rejects.toThrow(/hosted\.font_url_drift|hosted\.font_unserved: the local view requests Geist italic/);
    await expect(
      build(fonts, { fontsUrl: FONTS + "&family=Inter:wght@400" }),
    ).rejects.toThrow("hosted.font_unserved: the local view requests Inter");
  });
});
