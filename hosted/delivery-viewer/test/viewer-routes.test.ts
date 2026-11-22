import { describe, it, expect } from "vitest";
import { assets } from "../src/generated-assets";
import { digest } from "../src/protocol";
import { id, session, upload, request, sql } from "./helpers";
describe("trusted hosted shell boundary", () => {
  it("serves a fixed external-asset shell only for a committed capability", async () => {
    const publicId = id();
    expect((await request(`/d/${publicId}`, {}, null)).status).toBe(404);
    expect(
      await sql(
        publicId,
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%'",
      ),
    ).toEqual([]);
    await session(publicId);
    expect((await request(`/d/${publicId}`, {}, null)).status).toBe(404);
    await upload(publicId);
    const response = await request(`/d/${publicId}`, {}, null);
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toContain("text/html");
    expect(response.headers.get("Content-Security-Policy")).toBe(
      "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; connect-src 'self'; img-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    );
    const text = await response.text();
    expect(text).toContain('id="hosting-status"');
    expect(text).not.toMatch(/hosting-strip|hosting-times|<aside/);
    expect(text).not.toMatch(/https?:\/\//);
    expect(text).toContain('src="/assets/');
    expect(text).not.toContain("</script><b>Work</b>");
    expect(text).not.toContain("sourceFingerprint");
    expect(text).not.toContain(publicId);
    expect(text).not.toMatch(/<script(?![^>]*src=)[^>]*>[^<]+/);
  });
});

describe("fixed content-addressed asset routes", () => {
  it("serves only trusted exact build paths with MIME, privacy and immutable cache headers", async () => {
    // Both versions retain their renderer, viewer and stylesheet. They share eight fonts and three licences.
    expect(Object.keys(assets)).toHaveLength(2 * 3 + 8 + 3);
    const fonts = Object.values(assets).filter((asset) => asset.type === "font/woff2");
    expect(fonts).toHaveLength(8);
    expect(fonts.every((asset) => asset.encoding === "base64")).toBe(true);
    for (const [path, asset] of Object.entries(assets)) {
      const bytes =
        asset.encoding === "base64"
          ? Uint8Array.from(atob(asset.body), (c) => c.charCodeAt(0))
          : new TextEncoder().encode(asset.body);
      expect(path).toContain(await digest(bytes));
      expect(path).toMatch(/^\/assets\/[a-f0-9]{64}\.(js|css|woff2|txt)$/);
      const response = await request(path, {}, null);
      expect(response.status).toBe(200);
      const served = new Uint8Array(await response.arrayBuffer());
      expect(served).toEqual(bytes);
      if (asset.type === "font/woff2")
        expect(new TextDecoder().decode(served.slice(0, 4))).toBe("wOF2");
      expect(response.headers.get("Content-Type")).toBe(asset.type);
      expect(response.headers.get("Cache-Control")).toBe(
        "public, max-age=31536000, immutable",
      );
      expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
      expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
      expect(response.headers.get("X-Robots-Tag")).toBe(
        "noindex, nofollow, noarchive",
      );
      expect((await request(path, { method: "POST" }, null)).status).toBe(404);
    }
    // The stylesheet's fonts are these served files, and it names no other origin.
    const css = Object.values(assets).find((asset) => asset.type.startsWith("text/css"))!.body;
    const urls = [...css.matchAll(/url\(([^)]+)\)/g)].map((match) => match[1]);
    expect(urls.length).toBeGreaterThanOrEqual(8);
    for (const url of urls) expect(assets[url]?.type).toBe("font/woff2");
    expect(css).not.toMatch(/https?:\/\/|@import/);
    for (const family of ["Newsreader", "Geist", "Geist Mono"])
      expect(css).toContain(`@font-face { font-family: "${family}";`);
    for (const path of [
      "/assets/",
      "/assets/history.js",
      `/assets/${"0".repeat(64)}.js`,
      "/assets/../../secrets",
      "/d/unknown",
    ])
      expect((await request(path, {}, null)).status).toBe(404);
  });
});
