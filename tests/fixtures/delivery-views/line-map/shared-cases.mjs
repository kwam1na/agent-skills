// Builds the corpus's two shared records, `plan-shared` and `colead`, from the page lane's own fixtures:
// `node tests/fixtures/delivery-views/line-map/shared-cases.mjs`. Each case gets its document.json, plan.json and
// runs-commands.json, and a golden.json naming its clock and the chapters it pins, which recapture-goldens.mjs fills.
// Run it again after changing a case here, then recapture the goldens in their own commit.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { endorsement, headOf, item, minute, previewDelivery } from "../../../delivery_views_page/plan-fixtures.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AMA = "s-aaaaaaaaaaaa", BEN = "s-bbbbbbbbbbbb";
const write = (file, value) => writeFileSync(file, JSON.stringify(value, null, 2) + "\n");

/** A journal entry a seat wrote, as the shared record keeps it: its origin, and the entries of each seat it had seen. */
function entries(steps) {
  const counts = {};
  return steps.map(([at, seat, kind, data, extra = {}], index) => {
    counts[seat] = (counts[seat] ?? 0) + 1;
    const seen = Object.fromEntries(Object.entries(counts).filter(([other]) => other !== seat));
    return { id: index + 1, at: minute(at), kind, lane: null, subject: null, data,
      origin: { seat, seq: counts[seat], seen: extra.seen ?? seen }, ...(extra.answers ? { answers: extra.answers } : {}) };
  });
}

function shared(document, { seats, peers, steps, heldReason }) {
  document.state.meta.collab = { key: "SHOP-90", seats, peers, ...(heldReason ? { heldReason } : {}) };
  document.state.meta.phase = "planning";
  document.journal = entries(steps);
  return document;
}

const START = { base: { ref: "origin/main", head: headOf("main") } };
const CASES = {
  // A planning record two seats write: Ben's endorsement is of an older commit, Ben holds a section, feedback is open,
  // blocking and settled, and Ben's updates are held for another writer generation. Labels and feedback carry markup.
  "plan-shared": () => {
    const { document, plan } = previewDelivery({ rounds: 2,
      endorsements: [endorsement("Ama"), { ...endorsement("Ben", { stale: true }), label: "Ben <b>B</b>" }] });
    shared(document, {
      seats: { [AMA]: { label: "Ama", admitted: true, approver: true }, [BEN]: { label: "Ben <b>B</b>", admitted: true, approver: true } },
      peers: { [BEN]: { through: 4, fetchedAt: minute(355), heldReason: "generation_differs" } },
      steps: [
        [1, AMA, "record.started", START],
        [2, AMA, "seat.admitted", { seat: AMA, label: "Ama", approver: true }],
        [3, BEN, "seat.requested", { seat: BEN, label: "Ben <b>B</b>" }],
        [4, AMA, "seat.admitted", { seat: BEN, label: "Ben <b>B</b>", approver: true }, { answers: [3] }],
        [5, AMA, "scope.transferred", { scope: "plan:### SHOP-92. Address book export", from: AMA, to: BEN }],
        [6, BEN, "feedback.raised", { anchor: "## Decisions", text: "Per account <script>alert(1)</script>, or per device?", blocking: true }],
        [7, AMA, "feedback.raised", { anchor: "### SHOP-91. Saved addresses", text: "Name the cap on saved addresses.", blocking: false }],
        [8, BEN, "feedback.raised", { anchor: "### SHOP-92. Address book export", text: "CSV first.", blocking: false }],
        [9, BEN, "feedback.resolved", { outcome: "accepted", reason: "CSV it is <img src=x onerror=alert(1)>." }, { answers: [8] }],
        [10, BEN, "plan.endorsed", { commit: headOf("plan", 1) }],
        [11, AMA, "plan.endorsed", { commit: headOf("plan") }],
      ],
    });
    return { document, plan };
  },
  // A planning record every approver seat has endorsed, its queue handed to Ben and not yet taken up, a lane Ben holds,
  // and Ben's chain split.
  "colead": () => {
    const { document, plan } = previewDelivery({ rounds: 3, approval: "endorsed", endorsements: [endorsement("Ama"), endorsement("Ben")] });
    document.state.lanes.push({ id: "lane-2", title: "Lane two", focus: "", state: "idle", waitingOn: null, since: minute(0) });
    document.state.queue = [{ ...item("SHOP-91", "queued"), order: 1 }];
    shared(document, {
      seats: { [AMA]: { label: "Ama", admitted: true, approver: true }, [BEN]: { label: "Ben", admitted: true, approver: true },
        "s-cccccccccccc": { label: "Cy", admitted: true, approver: false } },
      peers: { [BEN]: { through: 3, fetchedAt: minute(350), heldReason: "seat_split" },
        "s-cccccccccccc": { through: 1, fetchedAt: minute(356), heldReason: null } },
      steps: [
        [1, AMA, "record.started", START],
        [2, AMA, "seat.admitted", { seat: AMA, label: "Ama", approver: true }],
        [3, BEN, "seat.requested", { seat: BEN, label: "Ben" }],
        [4, AMA, "seat.admitted", { seat: BEN, label: "Ben", approver: true }, { answers: [3] }],
        [5, "s-cccccccccccc", "seat.requested", { seat: "s-cccccccccccc", label: "Cy" }],
        [6, AMA, "seat.admitted", { seat: "s-cccccccccccc", label: "Cy", approver: false }, { answers: [5] }],
        [7, AMA, "scope.transferred", { scope: "lane:lane-2", from: AMA, to: BEN }],
        [8, BEN, "plan.endorsed", { commit: headOf("plan") }],
        [9, AMA, "plan.endorsed", { commit: headOf("plan") }],
        [10, AMA, "queue.stopped", { seat: AMA }],
        [11, AMA, "scope.transferred", { scope: "queue", from: AMA, to: BEN }],
      ],
    });
    return { document, plan };
  },
};

const runs = readFileSync(path.join(HERE, "plan-review", "runs-commands.json"), "utf8");
for (const [name, build] of Object.entries(CASES)) {
  const folder = path.join(HERE, name);
  mkdirSync(folder, { recursive: true });
  const { document, plan } = build();
  const pinned = ["seats", ...(document.journal.some((entry) => entry.kind === "feedback.raised") ? ["feedback"] : []), "plan"];
  write(path.join(folder, "document.json"), document);
  write(path.join(folder, "plan.json"), plan);
  writeFileSync(path.join(folder, "runs-commands.json"), runs);
  const golden = path.join(folder, "golden.json");
  if (!existsSync(golden)) {
    write(golden, { now: "2026-10-30T12:00:00Z", blocks: { "delivery-state": null, "delivery-runs": null, "delivery-plan": null },
      initialText: "", folds: { units: "" }, chapters: Object.fromEntries(pinned.map((id) => [id, ""])), details: {} });
  }
}
