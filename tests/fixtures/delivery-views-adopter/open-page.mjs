// Opens a rendered delivery-views page under jsdom, as the page lane does, and prints what a reader can
// reach: every link on the page and in each piece's detail, and whether any markup the record carried
// became an element.
//
//   node open-page.mjs <page.html> <hostile text>
import { readFileSync } from "node:fs";
import { openPage } from "../../delivery_views_page/support.mjs";

const [file, hostile] = process.argv.slice(2);
const page = readFileSync(file, "utf8");
const [first] = await openPage(page, { now: "2026-10-23T12:00:00Z" });
const subjects = [...first.app.querySelectorAll("[data-subject]")].map((node) => node.getAttribute("data-subject"));
const views = [first];
for (const subject of [...new Set(subjects)]) {
  const shown = await openPage(page, { now: "2026-10-23T12:00:00Z", steps: [{ click: `[data-subject="${subject}"]` }] });
  views.push(shown[1]);
}
const hrefs = new Set();
let elements = 0;
let shownAsText = false;
for (const view of views) {
  for (const link of view.app.querySelectorAll("a[href]")) hrefs.add(link.getAttribute("href"));
  elements += view.app.querySelectorAll("img, script, iframe").length;
  elements += [...view.app.querySelectorAll("b")].filter((node) => node.textContent === "bold").length;
  shownAsText ||= view.text.includes(hostile);
}
process.stdout.write(JSON.stringify({ subjects: [...new Set(subjects)].sort(), hrefs: [...hrefs].sort(), elements,
  shownAsText, head: page.slice(0, page.indexOf("<body>")) }));
