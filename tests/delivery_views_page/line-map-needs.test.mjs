// The "Now" chapter shows the supervisor's "Needs you" list: holding items first, then shown ones, each with its source
// and what clears it. A list from a stopped supervisor, one older than a pass, or one that can't be read isn't shown.
import { test } from "node:test";
import { expect, openPage, renderPage } from "./support.mjs";

const config = { spec: "delivery-views-config/1" };
const NOW = "2026-01-01T12:00:00Z";
function document() {
  return { state: { meta: { headline: "Fixture delivery" }, lanes: [], queue: [], planned: [] }, journal: [] };
}
function needs(over = {}) {
  return {
    spec: "delivery-needs-you/1", running: true, pass: 7, at: "2026-01-01T11:59:30Z", staleAfter: "2026-01-01T12:01:30Z",
    items: [
      { holding: false, source: "tracker ledger", text: "FX-9: the move \"Done\" isn't offered from Review",
        clears: "nothing: it stays listed for the friction note" },
      { holding: true, source: "tracker publisher", text: "The tracker publisher hasn't posted journal entries 12",
        clears: "the publisher posting them on a later pass" },
      { holding: true, source: "fullHealth", text: "fullHealth failed at abcdef0123: 1 test failed",
        clears: "a pass at a later base head, or lead-resolve full-health --why TEXT" },
    ],
    ...over,
  };
}
async function shown(list, now = NOW) {
  const [page] = await openPage(renderPage(document(), { config, needs: list }), { now });
  return page.app.querySelector(".needs-you");
}

test("holding items come first, then shown ones, each with its source and what clears it", async () => {
  const box = await shown(needs());
  expect(box.querySelector(".ah").textContent).toBe("Needs you");
  const groups = [...box.querySelectorAll(".needs-group")].map((group) => group.querySelector(".needs-kind").textContent);
  expect(groups).toEqual(["Holds the close", "Shown only"]);
  const rows = [...box.querySelectorAll("li")];
  expect(rows.map((row) => row.querySelector(".a1").textContent)).toEqual([
    "The tracker publisher hasn't posted journal entries 12",
    "fullHealth failed at abcdef0123: 1 test failed",
    "FX-9: the move \"Done\" isn't offered from Review",
  ]);
  expect(rows[1].querySelector(".a2").textContent).toBe("From fullHealth. Clears with a pass at a later base head, or lead-resolve full-health --why TEXT.");
  expect(rows[2].querySelector(".a2").textContent).toBe("From tracker ledger. Clears with nothing: it stays listed for the friction note.");
});

test("an empty current list says nothing needs you", async () => {
  const box = await shown(needs({ items: [] }));
  expect(box.querySelector(".needs-none").textContent).toBe("Nothing needs you; the supervisor's last pass found nothing.");
  expect(box.querySelector("li")).toBeNull();
});

test("a stopped supervisor's list is unavailable and none of its items show", async () => {
  const box = await shown(needs({ running: false, stoppedAt: "2026-01-01T11:59:50Z" }));
  expect(box.querySelector(".needs-none").textContent)
    .toBe("Unavailable: the supervisor isn't running, so its list isn't shown. lead-run --resume starts it again.");
  expect(box.querySelector("li")).toBeNull();
});

test("a list older than a pass is unavailable, and says how old", async () => {
  const box = await shown(needs(), "2026-01-01T12:05:00Z");
  expect(box.querySelector(".needs-none").textContent)
    .toBe("Unavailable: the supervisor's list is from 5m 30s ago, more than a pass old, so it isn't shown. Its supervisor may have stopped.");
  expect(box.querySelector("li")).toBeNull();
  expect((await shown(needs(), "2026-01-01T12:01:30Z")).querySelectorAll("li").length).toBe(3);
});

test("a list that can't be read is unavailable, and the rest of the page still draws", async () => {
  for (const bad of ["{not json", JSON.stringify({ spec: "other/1" }), JSON.stringify(needs({ items: [{ holding: "yes" }] })),
    JSON.stringify(needs({ staleAfter: "soon" })), JSON.stringify(needs({ items: "none" }))]) {
    const [page] = await openPage(renderPage(document(), { config, needs: bad }), { now: NOW });
    expect(page.app.querySelector(".needs-you .needs-none").textContent)
      .toBe("Unavailable: the supervisor's list can't be read, so it isn't shown.");
    expect(page.app.querySelector(".needs-you li")).toBeNull();
    expect(page.app.querySelector("h1.title").textContent).toBe("Nothing on the line yet.");
  }
});

test("without a supervisor's list the chapter has no Needs you box at all", async () => {
  const [page] = await openPage(renderPage(document(), { config }), { now: NOW });
  expect(page.app.querySelector(".needs-you")).toBeNull();
});

test("item text is shown as text, never as markup", async () => {
  const box = await shown(needs({ items: [{ holding: true, source: "<b>x</b>", text: "</script><img src=x onerror=alert(1)>", clears: "<i>y</i>" }] }));
  expect(box.querySelector("img")).toBeNull();
  expect(box.querySelector(".a1").textContent).toBe("</script><img src=x onerror=alert(1)>");
  expect(box.querySelector(".a2").textContent).toBe("From <b>x</b>. Clears with <i>y</i>.");
});

test("a live push takes the list from the page the server renders after it", async () => {
  const first = renderPage(document(), { config, needs: needs() });
  const later = renderPage(document(), { config, needs: needs({ running: false }) });
  const pushed = { ...document(), journal: [{ id: 1, at: NOW, kind: "decision.recorded", summary: "moved" }] };
  const [before, pending, after] = await openPage(first, { now: NOW, rendered: later, steps: [{ push: pushed }, { wait: 3100 }] });
  expect(before.app.querySelectorAll(".needs-you li").length).toBe(3);
  expect(pending.app.querySelectorAll(".needs-you li").length).toBe(3);
  expect(after.app.querySelector(".needs-you .needs-none").textContent).toContain("the supervisor isn't running");
});

test("an open page picks up a pass's new list on its timer, with no record push", async () => {
  const first = renderPage(document(), { config, needs: needs() });
  const later = renderPage(document(), { config, needs: needs({ at: "2026-01-01T12:00:30Z", staleAfter: "2026-01-01T12:02:30Z",
    items: needs().items.filter((item) => item.source !== "fullHealth") }) });
  const [before, after] = await openPage(first, { now: "2026-01-01T12:01:00Z", rendered: later, steps: [{ tick: true }, { wait: 3100 }] })
    .then((snapshots) => [snapshots[0], snapshots[2]]);
  expect(before.app.querySelectorAll(".needs-you li").length).toBe(3);
  expect([...after.app.querySelectorAll(".needs-you li .a1")].map((row) => row.textContent)).toEqual([
    "The tracker publisher hasn't posted journal entries 12", "FX-9: the move \"Done\" isn't offered from Review"]);
});

test("a list over the renderer's cap shows how many more it left out", async () => {
  const many = Array.from({ length: 102 }, (_, n) => ({ holding: true, source: "s", text: `item ${n}`, clears: "c" }));
  const box = await shown(needs({ items: many }));
  expect(box.querySelectorAll("li").length).toBe(100);
  expect(box.querySelector(".needs-more").textContent).toBe("2 more not shown.");
});

test("a refresh whose page has no list drops the one shown", async () => {
  const first = renderPage(document(), { config, needs: needs() });
  const later = renderPage(document(), { config });
  const snapshots = await openPage(first, { now: NOW, rendered: later, steps: [{ tick: true }, { wait: 3100 }] });
  expect(snapshots[0].app.querySelectorAll(".needs-you li").length).toBe(3);
  expect(snapshots[2].app.querySelector(".needs-you")).toBeNull();
});
