// The one formatter every view's prose goes through (richWords, in run-summary.js): it reads only what the words mark
// themselves, never a guess from a word's shape, and links only through the hooks its host gives it.
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { JSDOM } from "jsdom";

import { ROOT, expect } from "./support.mjs";

const SOURCE = readFileSync(path.join(ROOT, "skills", "delivery-views", "assets", "run-summary.js"), "utf8");

function formatter() {
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  dom.window.eval(SOURCE);
  return dom.window;
}
// Each node as [tag or "#text", class, text, href].
function parts(nodes) {
  return Array.from(nodes, (node) => node.nodeType === 3
    ? ["#text", "", node.textContent, null]
    : [node.tagName.toLowerCase(), node.className, node.textContent, node.getAttribute("href")]);
}

test("a backticked name reads as code; a name left bare stays plain words whatever its shape", () => {
  const window = formatter();
  expect(parts(window.richWords("Routing through CalloutsService.create bumps `anyCalloutCount` for OnCall."))).toEqual([
    ["#text", "", "Routing through CalloutsService.create bumps ", null],
    ["code", "prose-code", "anyCalloutCount", null],
    ["#text", "", " for OnCall.", null],
  ]);
});

test("a parenthesised line reference steps back to an aside, each reference linked only where the host links it", () => {
  const window = formatter();
  const links = { "docs/plan.md:84-89": "https://forge.example/acme/shop/src/abc1234/docs/plan.md#lines-84" };
  const nodes = window.richWords("The plan names it (docs/plan.md:84-89, src/app.ts:12).", { file: (ref) => links[ref] ?? null });
  expect(parts(nodes)).toEqual([
    ["#text", "", "The plan names it ", null],
    ["span", "prose-refs", "docs/plan.md:84-89, src/app.ts:12", null],
    ["#text", "", ".", null],
  ]);
  expect(parts([...nodes[1].childNodes])).toEqual([
    ["a", "prose-ref", "docs/plan.md:84-89", "https://forge.example/acme/shop/src/abc1234/docs/plan.md#lines-84"],
    ["#text", "", ", ", null],
    ["span", "prose-ref", "src/app.ts:12", null],
  ]);
});

test("a reference the host links to anything but https stays words", () => {
  const window = formatter();
  const nodes = window.richWords("See (a.md:3).", { file: () => "javascript:alert(1)" });
  expect(parts([...nodes[1].childNodes])).toEqual([["span", "prose-ref", "a.md:3", null]]);
});

test("parentheses that are not line references stay words", () => {
  const window = formatter();
  expect(parts(window.richWords("It waits (PD1, Rollout step 2) on infra (phase 2)."))).toEqual([
    ["#text", "", "It waits (PD1, Rollout step 2) on infra (phase 2).", null],
  ]);
});

test("commits, change requests and tickets link through the host's hooks, and stay words without them", () => {
  const window = formatter();
  const said = "Fixed in 973263f for SHOP-12, see https://forge.example/acme/shop/pull/7.";
  expect(parts(window.richWords(said))).toEqual([
    ["#text", "", "Fixed in ", null],
    ["#text", "", "973263f", null],
    ["#text", "", " for SHOP-12, see ", null],
    ["#text", "", "https://forge.example/acme/shop/pull/7", null],
    ["#text", "", ".", null],
  ]);
  const document = window.document;
  const link = (className, text) => { const a = document.createElement("a"); a.className = className; a.textContent = text; a.setAttribute("href", "https://forge.example/x"); return a; };
  const linked = window.richWords(said, {
    tickets: ["SHOP-12", "SHOP-1"],
    ticket: (id) => link("issue-link", id),
    commit: (sha) => link("fact-ref", sha),
    change: () => link("change-link", "#7"),
  });
  expect(parts(linked)).toEqual([
    ["#text", "", "Fixed in ", null],
    ["a", "fact-ref", "973263f", "https://forge.example/x"],
    ["#text", "", " for ", null],
    ["a", "issue-link", "SHOP-12", "https://forge.example/x"],
    ["#text", "", ", see ", null],
    ["a", "change-link", "#7", "https://forge.example/x"],
    ["#text", "", ".", null],
  ]);
});

test("a commit id inside backticks stays code, and words are never read as markup", () => {
  const window = formatter();
  expect(parts(window.richWords("Pinned `973263f` <b>not bold</b>", { commit: () => null }))).toEqual([
    ["#text", "", "Pinned ", null],
    ["code", "prose-code", "973263f", null],
    ["#text", "", " <b>not bold</b>", null],
  ]);
});
