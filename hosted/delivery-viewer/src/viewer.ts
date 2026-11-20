import {
  createViewStore,
  type PublicProjection,
  type ViewStatus,
} from "./view-store";

const match = /^\/d\/([a-f0-9]{64})$/.exec(location.pathname);
const statusNode = document.getElementById("hosting-status");
const app = document.getElementById("app");
const entry = document.querySelector<HTMLScriptElement>(
  "script[data-renderer]",
);
// A publication that is current, or a completed one saved and retained while its connection holds, shows only as the
// hero's live line. Every other state is said there in words too, without interaction.
const quiet = (value: ViewStatus) =>
  value.kind === "current" || (value.kind === "retained" && !value.problem);
/** The receipt's times, as the hero's live line offers them on hover or focus. */
function hostingTimes(value: ViewStatus): string {
  const r = value.receipt;
  return r
    ? `Source confirmed ${new Date(r.sourceConfirmedAt).toLocaleString()} · Published ${new Date(r.publishedAt).toLocaleString()} · Revision ${r.epoch}.${r.sequence}`
    : "";
}
if (match && statusNode && app && entry) {
  const store = createViewStore(match[1]);
  let started = false;
  let assetFailed = false;
  let current: ViewStatus = store.status();
  // The hero's live line as drawn on the page. The renderer redraws it and hands each new one to the hook below before
  // it is placed, so a status change looks it up afresh rather than trusting a line that may never have been placed.
  const drawn = () => app!.querySelector(".fact.since");
  function words(value: ViewStatus) {
    return assetFailed
      ? { kind: "unavailable", detail: "Viewer assets unavailable · reload to retry", quiet: false }
      : { kind: value.kind, detail: value.detail, quiet: quiet(value) };
  }
  /** Puts the hosting status into a live line the renderer drew: the times on its label, the words when not quiet. */
  function paint(fact: Element) {
    const label = fact.querySelector(".fact-label");
    const handle = label?.firstElementChild as HTMLElement | null | undefined;
    if (!label || !handle) return;
    const { kind, detail, quiet: hushed } = words(current);
    const times = hostingTimes(current);
    label.querySelector(".hosted-times")?.remove();
    label.querySelector(".hosted-words")?.remove();
    fact.setAttribute("data-hosting", kind);
    if (times) {
      // data-tab is the renderer's own focus mark: a redraw puts keyboard focus back on the new label.
      handle.setAttribute("tabindex", "0");
      handle.setAttribute("data-tab", "hosting");
      handle.setAttribute("aria-describedby", "hosting-times");
      const tip = document.createElement("span");
      tip.className = "hosted-times";
      tip.id = "hosting-times";
      tip.setAttribute("role", "tooltip");
      tip.textContent = times;
      label.appendChild(tip);
    } else {
      handle.removeAttribute("tabindex");
      handle.removeAttribute("data-tab");
      handle.removeAttribute("aria-describedby");
    }
    if (!hushed) {
      // The live region below announces the words; this copy is what a sighted reader sees.
      const said = document.createElement("span");
      said.className = "hosted-words";
      said.setAttribute("aria-hidden", "true");
      said.textContent = detail;
      label.appendChild(said);
    }
  }
  Object.assign(globalThis, {
    deliveryHostedLine(fact: Element) {
      paint(fact);
      queueMicrotask(place);
    },
  });
  function showStatus(value: ViewStatus) {
    current = value;
    const { kind, detail } = words(value);
    if (statusNode!.textContent !== detail) statusNode!.textContent = detail;
    statusNode!.dataset.state = kind;
    const line = drawn();
    if (line) paint(line);
    place();
  }
  /**
   * The live region is always there to announce. It is seen on its own only while no live line is drawn, and not when
   * the page's own words already say the same: its loading line, or its note that the viewer assets failed.
   */
  function place() {
    const said = !!drawn() || assetFailed || current.kind === "loading";
    statusNode!.classList.toggle("hosted-announce", said);
  }
  function seed(body: PublicProjection) {
    // The blocks the local page embeds, by the ids the corpus renderer reads them from.
    const fields: Record<string, unknown> = {
      state: body.document,
      runs: body.runs,
      reviews: body.reviews,
      links: body.links,
      view: body.view,
      plan: body.plan,
      needs: body.needs,
      files: body.files,
      "left-open": body.leftOpen,
      integration: body.integration,
    };
    for (const [key, value] of Object.entries(fields)) {
      const id = `delivery-${key}`;
      const existing = document.getElementById(id);
      if (value === undefined || value === null) {
        existing?.remove();
        continue;
      }
      if (existing) {
        existing.textContent = JSON.stringify(value);
        continue;
      }
      const block = document.createElement("script");
      block.type = "application/json";
      block.id = id;
      block.textContent = JSON.stringify(value);
      document.body.appendChild(block);
    }
    document.title =
      typeof body.view.title === "string" ? body.view.title : "Hosted delivery";
  }
  store.onHostingStatus(showStatus);
  store.subscribe(() => {
    const body = store.projection();
    if (!body) return;
    document.title =
      typeof body.view.title === "string" ? body.view.title : "Hosted delivery";
    // Keep inert blocks current even while the first renderer request is delayed.
    // The active renderer still receives its coherent native subscription push.
    seed(body);
    if (started) return;
    started = true;
    Object.assign(globalThis, { deliveryStateStore: store });
    const renderer = document.createElement("script");
    renderer.src = entry!.dataset.renderer!;
    renderer.addEventListener("load", () =>
      app!.setAttribute("aria-busy", "false"),
    );
    renderer.addEventListener("error", () => {
      assetFailed = true;
      app!.textContent =
        "Viewer assets unavailable. Reload this page to retry.";
      app!.setAttribute("aria-busy", "false");
      showStatus(store.status());
    });
    document.head.appendChild(renderer);
  });
  store.start();
  addEventListener("pagehide", () => store.stop());
  addEventListener("pageshow", () => store.start());
}
