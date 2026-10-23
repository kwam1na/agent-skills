// The delivery history: every listed run and export, newest first, each opening its run summary in place.
(function () {
  "use strict";
  var history = { rows: [], notes: [] };
  try { history = JSON.parse(document.getElementById("delivery-history").textContent); } catch (error) { history = { rows: [], notes: ["The history could not be read."] }; }
  var VIEW = {};
  try { VIEW = JSON.parse(document.getElementById("delivery-history-view").textContent) || {}; } catch (error) { VIEW = {}; }
  var app = document.getElementById("app");
  function el(tag, className, value) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (value !== undefined && value !== null) { node.textContent = String(value); }
    return node;
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i += 1) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) { continue; }
      parent.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return parent;
  }
  function text(value) { return typeof value === "string" ? value : ""; }
  function list(value) { return Array.isArray(value) ? value : []; }
  function at(value) { return typeof value === "string" && value.length >= 16 ? value.slice(0, 10) + " " + value.slice(11, 16) : (text(value) || "—"); }
  function duration(total) {
    if (typeof total !== "number" || !isFinite(total) || total < 0) { return "—"; }
    var s = Math.round(total);
    if (s < 60) { return s + "s"; }
    if (s < 3600) { return Math.floor(s / 60) + "m"; }
    if (s < 86400) { return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m"; }
    return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
  }
  function findings(value) {
    if (!value || typeof value !== "object") { return "—"; }
    return ["P0", "P1", "P2", "P3"].map(function (severity) { return Number(value[severity]) || 0; }).join("/");
  }
  function cell(className, label, value) { return add(el("span", className), el("span", "label", label + " "), value); }
  var served = typeof location === "object" && /^https?:$/.test(location.protocol);
  var lineMap = null;
  if (text(VIEW.lineMap)) {
    lineMap = el("a", null, "Back to the line map");
    lineMap.setAttribute("href", served ? "/view/" + encodeURIComponent(VIEW.lineMap) : VIEW.lineMap);
  }
  var sources = text(VIEW.exports) ? "Runs from the delivery product and the exports tracked under " + VIEW.exports : "Runs from the delivery product";
  var page = add(el("div", "page"),
    add(el("div", "kicker"), el("span", null, "Delivery history"), lineMap),
    el("h1", "title", "Past deliveries and runs, newest first."),
    el("p", "say", sources + ", as of " + at(history.asOf) + " UTC. Reload for newer."));
  var notes = list(history.notes);
  if (notes.length) { page.appendChild(add.apply(null, [el("ul", "notes")].concat(notes.map(function (note) { return el("li", null, text(note)); })))); }
  var runs = add(el("div", "runs"), add(el("div", "head"),
    el("span", null, "Ticket"), el("span", null, "State"), el("span", null, "Started"), el("span", null, "Last seen"),
    el("span", null, "Duration"), el("span", null, "Rounds"), el("span", null, "Findings P0/P1/P2/P3"), el("span", null, "Delivery")));
  var rows = list(history.rows);
  if (!rows.length) { runs.appendChild(el("p", "say", "No runs or exports are recorded yet.")); }
  rows.forEach(function (row) {
    var summary = row && typeof row.summary === "object" && row.summary ? row.summary : {};
    var item = el("div", "row");
    item.setAttribute("data-run", text(row.key));
    var button = el("button");
    button.setAttribute("type", "button");
    button.setAttribute("aria-expanded", "false");
    var ticket = el("span", "ticket", text(summary.ticket) || text(row.label) || text(summary.runId) || text(row.key));
    var under = row.source === "telemetry" ? "telemetry record" + (text(row.label) && text(summary.ticket) ? " · " + text(row.label) : "")
      : text(summary.runId) || text(row.file);
    var who = add(el("span", "who"), ticket, under && under !== ticket.textContent ? el("small", null, under) : null);
    var state = summary.open === true ? el("span", "open", "Open") : summary.open === false ? el("span", null, "Ended") : el("span", null, "Unknown");
    var delivery = row.delivery && typeof row.delivery === "object"
      ? [text(row.delivery.headline) || text(row.delivery.parent) || "This delivery", text(row.delivery.subject)].filter(Boolean).join(" · ")
      : "—";
    add(button, who, cell(null, "State", state), cell("when", "Started", at(summary.startedAt)), cell("when", "Last seen", at(summary.lastAt)),
      cell("when", "Duration", duration(summary.durationSeconds)), cell("when", "Rounds", typeof summary.rounds === "number" ? String(summary.rounds) : "—"),
      cell("findings", "Findings", findings(summary.findings)), add(el("span", "delivery"), el("span", "label", "Delivery "), delivery));
    if (text(row.note)) { button.appendChild(el("span", "note", text(row.note))); }
    item.appendChild(button);
    var detail = el("div", "detail");
    detail.hidden = true;
    button.addEventListener("click", function () {
      var opening = detail.hidden;
      if (opening && !detail.firstChild) { detail.appendChild(runSummaryNode(summary, { lenses: VIEW.lenses })); }
      detail.hidden = !opening;
      button.setAttribute("aria-expanded", opening ? "true" : "false");
    });
    add(runs, item, detail);
  });
  app.replaceChildren(add(page, runs));
})();
