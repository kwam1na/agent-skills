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
  it.each([
    ["font", "geist/geist-latin-wght-normal.woff2"],
    ["licence", "newsreader/OFL.txt"],
  ])("fails the build when a %s file's bytes change from its recorded digest", async (kind, path) => {
    const { dir, url } = await copy();
    try {
      await writeFile(join(dir, path), "not the file");
      await expect(build(url)).rejects.toThrow(`hosted.font_digest: ${kind} ${path} does not match its recorded digest`);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
  it("fails the build when sources.json lists a file that is not woff2", async () => {
    const { dir, url } = await copy();
    try {
      const sources = JSON.parse(await readFile(join(dir, "sources.json"), "utf8"));
      sources.families[1].files[0].path = "geist/geist-latin-wght-normal.ttf";
      await writeFile(join(dir, "sources.json"), JSON.stringify(sources));
      await expect(build(url)).rejects.toThrow("hosted.font_type: geist/geist-latin-wght-normal.ttf is not a woff2 file");
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
  it("reads a family with no axes as its regular face, and refuses a link it cannot read face by face", () => {
    expect(requestedFaces("https://fonts.googleapis.com/css2?family=Inter&display=swap")).toEqual([
      { family: "Inter", style: "normal", weight: 400 },
    ]);
    expect(requestedFaces("https://fonts.googleapis.com/css2?family=Geist+Mono:wght@500")).toEqual([
      { family: "Geist Mono", style: "normal", weight: 500 },
    ]);
    for (const [link, error] of [
      ["fonts.googleapis.com/css2?family=Geist", "hosted.font_url_drift: the local FONTS link is not a URL"],
      ["https://fonts.googleapis.com/css2?display=swap", "hosted.font_url_drift: the local FONTS link names no family"],
      ["https://fonts.googleapis.com/css2?family=Geist:wght@100..900", "hosted.font_url_drift: Geist has a ranged weight 100..900"],
      ["https://fonts.googleapis.com/css2?family=Geist:ital", "hosted.font_url_drift: Geist names no weights"],
    ])
      expect(() => requestedFaces(link), link).toThrow(error);
  });
  it("fails the build when the stylesheet or the local view names a family or style it does not serve", async () => {
    await expect(
      build(fonts, { stylesheet: STYLESHEET.replace('--mono: "Geist Mono"', '--mono: "Fira Code"') }),
    ).rejects.toThrow("hosted.font_unserved: the stylesheet names Fira Code");
    const italic = FONTS.replace("family=Geist:wght@400;500;600", "family=Geist:ital,wght@0,400;1,400");
    expect(italic).not.toBe(FONTS);
    expect(requestedFaces(italic)).toContainEqual({ family: "Geist", style: "italic", weight: 400 });
    await expect(build(fonts, { fontsUrl: italic })).rejects.toThrow(
      "hosted.font_unserved: the local view requests Geist italic, which sources.json does not serve",
    );
    await expect(
      build(fonts, { fontsUrl: FONTS + "&family=Inter:wght@400" }),
    ).rejects.toThrow("hosted.font_unserved: the local view requests Inter");
  });
});
