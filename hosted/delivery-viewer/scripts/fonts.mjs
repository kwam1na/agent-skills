// The hosted page's fonts: the families, weights and styles the local view asks Google Fonts for, served instead from
// files this package carries, so the page's policy only has to allow font-src 'self'.
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

/** What the local view's Google Fonts URL asks for: one { family, style, weight } per requested face. */
export function requestedFaces(fontsUrl) {
  let url;
  try {
    url = new URL(fontsUrl);
  } catch {
    throw Error("hosted.font_url_drift: the local FONTS link is not a URL");
  }
  const families = url.searchParams.getAll("family");
  if (!families.length) throw Error("hosted.font_url_drift: the local FONTS link names no family");
  const faces = [];
  for (const spec of families) {
    const [name, axesPart] = spec.split(":");
    if (!name) throw Error("hosted.font_url_drift: a family in the local FONTS link has no name");
    if (!axesPart) {
      faces.push({ family: name, style: "normal", weight: 400 });
      continue;
    }
    const [axisList, tuples] = axesPart.split("@");
    const axes = axisList.split(",");
    const ital = axes.indexOf("ital");
    const wght = axes.indexOf("wght");
    if (wght < 0 || !tuples) throw Error(`hosted.font_url_drift: ${name} names no weights`);
    for (const tuple of tuples.split(";")) {
      const values = tuple.split(",");
      if (values.length !== axes.length) throw Error(`hosted.font_url_drift: ${name} has a malformed face ${tuple}`);
      const weight = Number(values[wght]);
      if (!Number.isInteger(weight)) throw Error(`hosted.font_url_drift: ${name} has a ranged weight ${values[wght]}`);
      faces.push({ family: name, style: ital >= 0 && values[ital] === "1" ? "italic" : "normal", weight });
    }
  }
  return faces;
}

/** The first family each of the stylesheet's type variables names, as `--display: "Newsreader", ...` writes it. */
export function stylesheetFamilies(css) {
  const named = new Set();
  for (const match of css.matchAll(/--(display|text|mono)\s*:\s*"([^"]+)"/g)) named.add(match[2]);
  return [...named];
}

/**
 * The @font-face rules and the asset files for the requested faces, from `sources.json` in `fontsDir`. Every file is
 * checked against its recorded digest, and every face the local view requests and every family the stylesheet names
 * must be served, or the build stops with a named error. `asset(bytes, extension, type)` returns the served path.
 */
export async function buildFonts({ fontsDir, fontsUrl, stylesheet, asset }) {
  let sources;
  try {
    sources = JSON.parse(await readFile(new URL("sources.json", fontsDir), "utf8"));
  } catch (error) {
    throw Error(`hosted.font_sources_unreadable: ${error.message}`);
  }
  const read = async (path, kind) => {
    let bytes;
    try {
      bytes = await readFile(new URL(path, fontsDir));
    } catch {
      throw Error(`hosted.font_missing: ${kind} ${path} is listed in sources.json but not on disk`);
    }
    return bytes;
  };
  const families = new Map();
  for (const family of sources.families ?? []) families.set(family.family, family);
  for (const name of stylesheetFamilies(stylesheet)) {
    if (!families.has(name)) throw Error(`hosted.font_unserved: the stylesheet names ${name}, which sources.json does not serve`);
  }
  const faces = requestedFaces(fontsUrl);
  const rules = [];
  const licences = [];
  for (const family of families.values()) {
    const licence = await read(family.license?.path ?? "", "licence");
    if (sha256(licence) !== family.license.sha256) throw Error(`hosted.font_digest: licence ${family.license.path} does not match its recorded digest`);
    licences.push(`${family.family} ${family.license.id} ${asset(licence, "txt", "text/plain; charset=utf-8")}`);
  }
  for (const face of faces) {
    const family = families.get(face.family);
    if (!family) throw Error(`hosted.font_unserved: the local view requests ${face.family}, which sources.json does not serve`);
    const files = family.files.filter((file) => file.style === face.style);
    if (!files.length) throw Error(`hosted.font_unserved: the local view requests ${face.family} ${face.style}, which sources.json does not serve`);
    for (const file of files) {
      if (!/\.woff2$/.test(file.path)) throw Error(`hosted.font_type: ${file.path} is not a woff2 file`);
      const bytes = await read(file.path, "font");
      if (sha256(bytes) !== file.sha256) throw Error(`hosted.font_digest: font ${file.path} does not match its recorded digest`);
      const path = asset(bytes, "woff2", "font/woff2");
      rules.push(
        `@font-face { font-family: "${face.family}"; font-style: ${face.style}; font-weight: ${face.weight}; font-display: swap; src: url(${path}) format("woff2"); unicode-range: ${file.unicodeRange}; }`,
      );
    }
  }
  return `/* Fonts served from this origin under the SIL Open Font License 1.1: ${licences.join("; ")} */\n${rules.join("\n")}\n`;
}
