// When lanes share files by lines, "Who's doing what" offers "Who holds which files", closed by default: the claimed
// files by area, each solid where its lane changed it and hollow where it didn't, each shared file split by its lanes'
// stretches, and a shared file's reading. A lane whose branch can't be read is said so, with nothing of it drawn as
// changed. With no region claims there is no such view.
import { test } from "node:test";
import { expect, openPage, renderPage } from "./support.mjs";

const config = { spec: "delivery-views-config/1" };
const NOW = "2026-11-18T12:00:00Z";
const lane = (id, title) => ({ id, title, focus: "", state: "working", waitingOn: null, since: "2026-11-18T10:00:00Z" });

function record(regions = true) {
  return {
    state: {
      meta: { headline: "Files" }, queue: [], lanes: [lane("lane-1", "Waits"), lane("lane-2", "Renderer")],
      planned: [{ subject: "S-1", lane: "lane-1", stage: "building" }, { subject: "S-2", lane: "lane-2", stage: "building" }],
      laneOwnership: { "lane-1": ["src/one*", "docs/*"], "lane-2": ["src/two*"] },
      laneRegions: regions ? [{ lane: "lane-1", path: "src/shared.py", baseHead: "a".repeat(40), start: 1, end: 20 },
                              { lane: "lane-2", path: "src/shared.py", baseHead: "a".repeat(40), start: 20, end: 41 }] : [],
    },
    journal: [],
  };
}

const change = (start, added, removed, name, inside) => ({ old: start, removed, start, added, inside, ...(name ? { name } : {}) });
const FILES = {
  spec: "delivery-lane-changes/1", at: "2026-11-18T11:59:30Z", base: "b".repeat(40),
  lanes: {
    "lane-1": { story: "S-1", branch: "claude/S-1", head: "c".repeat(40), base: "b".repeat(40), mergeBase: "b".repeat(40),
                readAt: "2026-11-18T11:59:00Z", files: [
                  { path: "src/one.py", holds: "file", changes: [change(2, 1, 1, "one", true)] },
                  { path: "src/shared.py", holds: "span", changes: [change(5, 1, 1, "held", true), change(7, 2, 0, "held", true),
                                                                    change(30, 3, 0, null, false)] }] },
    "lane-2": { story: "S-2", branch: "claude/S-2", head: "d".repeat(40), base: "b".repeat(40),
                unreadable: "git merge-base failed: no common history" },
  },
  files: [
    { path: "src/shared.py", lines: 40, spans: [{ lane: "lane-1", start: 1, end: 20 }, { lane: "lane-2", start: 20, end: 41 }] },
    { path: "docs/guide.md", lines: 5, lanes: ["lane-1"] },
    { path: "src/one.py", lines: 2, lanes: ["lane-1"] },
    { path: "src/two.js", lines: 3, lanes: ["lane-2"] },
  ],
};

const marks = (nodes) => nodes.map((node) => [node.getAttribute("data-path") || node.getAttribute("data-lane"),
  ["solid", "hollow", "unread"].find((mark) => node.classList.contains(mark))]);

test("the files view is closed by default and shows the claimed files by area, solid, hollow and unreadable", async () => {
  const [page] = await openPage(renderPage(record(), { config, files: FILES }), { now: NOW });
  const fold = page.app.querySelector("#who details.files-fold") || page.app.querySelector("details.files-fold");
  expect(Boolean(fold)).toBe(true);
  expect(fold.hasAttribute("open")).toBe(false);
  expect(fold.querySelector("summary").textContent).toBe("Who holds which files");
  expect([...fold.querySelectorAll(".files-area h4")].map((h) => h.textContent)).toEqual(["src/", "docs/"]);
  expect(marks([...fold.querySelectorAll(".file.whole")])).toEqual([
    ["src/one.py", "solid"], ["src/two.js", "unread"], ["docs/guide.md", "hollow"]]);
  const shared = fold.querySelector("button.file.shared");
  expect(shared.getAttribute("data-path")).toBe("src/shared.py");
  expect(marks([...shared.querySelectorAll(".stretch")])).toEqual([["lane-1", "solid"], ["lane-2", "unread"]]);
  const [first, second] = shared.querySelectorAll(".stretch");
  expect([first.style.left, first.style.width, second.style.left, second.style.width]).toEqual(["0%", "47.5%", "47.5%", "52.5%"]);
  expect([...fold.querySelectorAll(".files-unread")].map((p) => p.textContent)).toEqual([
    "Renderer's branch claude/S-2 can't be read, so none of its changes are shown: git merge-base failed: no common history"]);
  expect(fold.querySelector(".file-reading")).toBeNull();
});

test("a shared file opens its reading: a ruler of each lane's stretches and changes, and what each lane changed", async () => {
  const [, opened, closed] = await openPage(renderPage(record(), { config, files: FILES }), { now: NOW, steps: [
    { click: "button.file.shared" }, { click: "button.file.shared" }] });
  const reading = opened.app.querySelector(".file-reading");
  expect(reading.getAttribute("data-path")).toBe("src/shared.py");
  expect(reading.querySelector("h4").textContent).toBe("src/shared.py");
  expect([...reading.querySelectorAll(".ruler .stretch")].map((s) => [s.getAttribute("data-lane"), s.style.top, s.style.height]))
    .toEqual([["lane-1", "0%", "47.5%"], ["lane-2", "47.5%", "52.5%"]]);
  expect([...reading.querySelectorAll(".ruler .tick")].map((t) => [t.getAttribute("data-lane"), t.classList.contains("out"), t.style.top]))
    .toEqual([["lane-1", false, "10%"], ["lane-1", false, "15%"], ["lane-1", true, "72.5%"]]);
  expect([...reading.querySelectorAll(".file-lanes li")].map((li) => li.textContent)).toEqual([
    "Waits · held +3 −1, outside any function +3 −0", "Renderer · its branch can't be read"]);
  expect(opened.app.querySelector("button.file.shared").getAttribute("aria-expanded")).toBe("true");
  expect(closed.app.querySelector(".file-reading")).toBeNull();
});

test("with no region claims the page has no files view, whatever the branch read wrote", async () => {
  const [page] = await openPage(renderPage(record(false), { config, files: FILES }), { now: NOW });
  expect(page.app.querySelector(".files-fold")).toBeNull();
  expect(page.text).not.toContain("Who holds which files");
});

test("before any branch read, and with one that can't be read, the view says so and draws no files", async () => {
  const [none] = await openPage(renderPage(record(), { config }), { now: NOW });
  expect(none.app.querySelector(".files-fold .say").textContent).toBe("No pass has read the lanes' branches yet.");
  const [bad] = await openPage(renderPage(record(), { config, files: "{not json" }), { now: NOW });
  expect(bad.app.querySelector(".files-fold .say").textContent).toBe("The lanes' changes can't be read.");
  expect(bad.app.querySelector(".file")).toBeNull();
});
