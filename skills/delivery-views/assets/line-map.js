// The reader's light or dark pick, kept in this browser only; with none, the page follows the system.
(function () {
  "use strict";
  // Keyed by the state directory, so two repositories served from one address never share it.
  var KEY = "delivery-views:theme";
  try { KEY = JSON.parse(document.getElementById("delivery-view").textContent).themeKey || KEY; } catch (error) { /* the default key */ }
  var root = document.documentElement;
  var picks = Array.prototype.slice.call(document.querySelectorAll("[data-theme-pick]"));
  function stored() { try { return localStorage.getItem(KEY); } catch (error) { return null; } }
  function current() {
    var picked = root.getAttribute("data-theme");
    if (picked === "light" || picked === "dark") { return picked; }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  // The mode in use is the pressed one, whether the reader picked it or the system did.
  function mark() { picks.forEach(function (pick) { pick.setAttribute("aria-pressed", String(pick.getAttribute("data-theme-pick") === current())); }); }
  var saved = stored();
  if (saved === "light" || saved === "dark") { root.setAttribute("data-theme", saved); }
  mark();
  if (window.matchMedia) {
    var system = window.matchMedia("(prefers-color-scheme: dark)");
    if (system.addEventListener) { system.addEventListener("change", mark); }
  }
  picks.forEach(function (pick) {
    pick.addEventListener("click", function () {
      var next = pick.getAttribute("data-theme-pick");
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (error) { /* the pick lasts until the page reloads */ }
      mark();
    });
  });
})();
(function () {
  "use strict";
  var embedded = null;
  try { embedded = JSON.parse(document.getElementById("delivery-state").textContent); } catch (error) { embedded = null; }
  var app = document.getElementById("app");
  var connection = "saved";
  var shown = embedded;
  var selected = null;
  var feedFilter = "all";
  var feedAll = false;
  // What the page last showed, so a change can be animated rather than just swapped in.
  var prev = { stages: null, feed: null, progress: null, lanes: null, open: null, byLane: null, parked: null, dots: null, selected: null };
  var lastGood = null;
  var nativeReviews = {};
  try { nativeReviews = JSON.parse(document.getElementById("delivery-reviews").textContent); } catch (error) { nativeReviews = {}; }
  var runs = {};
  try { runs = JSON.parse(document.getElementById("delivery-runs").textContent); } catch (error) { runs = {}; }
  if (runs === null || typeof runs !== "object" || Array.isArray(runs)) { runs = {}; }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
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
  // Lookup tables carry no inherited names, so a record value like "constructor" finds nothing.
  function table(entries) {
    var lookup = Object.create(null);
    Object.keys(entries).forEach(function (key) { lookup[key] = entries[key]; });
    return lookup;
  }
  function subjectOf(item) { return item.subject === undefined || item.subject === null ? "" : String(item.subject); }
  function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function text(value) { return typeof value === "string" ? value : ""; }
  function short(head) { return typeof head === "string" && head.length >= 7 ? head.slice(0, 7) : text(head); }
  function seconds(at) { var t = typeof at === "string" ? Date.parse(at) : NaN; return isNaN(t) ? null : t / 1000; }
  function reduced() { return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function later(callback) {
    if (typeof requestAnimationFrame === "function") { requestAnimationFrame(function () { requestAnimationFrame(callback); }); } else { callback(); }
  }
  function replay(node, cls) { node.classList.remove(cls); void node.offsetWidth; node.classList.add(cls); }
  function countTo(node, from, to) {
    if (reduced() || from === null || from === to || typeof requestAnimationFrame !== "function" || typeof performance !== "object") {
      node.firstChild.textContent = String(to);
      return;
    }
    var start = performance.now();
    var step = function (t) {
      var k = Math.min(1, (t - start) / 900);
      node.firstChild.textContent = String(Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3))));
      if (k < 1) { requestAnimationFrame(step); }
    };
    requestAnimationFrame(step);
    replay(node, "ticking");
  }

  // How the page is configured, and the links the configuration allows, worked out by the renderer: the page
  // links a ticket id, a change request or a fix commit only when the renderer gave it an address.
  var VIEW = {};
  try { VIEW = JSON.parse(document.getElementById("delivery-view").textContent); } catch (error) { VIEW = {}; }
  if (!isObject(VIEW)) { VIEW = {}; }
  var LINKS = { issues: {}, changes: {}, commits: {} };
  function readLinks(value) {
    if (!isObject(value)) { return; }
    LINKS = { issues: table(isObject(value.issues) ? value.issues : {}), changes: table(isObject(value.changes) ? value.changes : {}),
      commits: table(isObject(value.commits) ? value.commits : {}) };
  }
  try { readLinks(JSON.parse(document.getElementById("delivery-links").textContent)); } catch (error) { /* nothing is linked */ }
  function issueHref(id) { return typeof LINKS.issues[id] === "string" ? LINKS.issues[id] : null; }
  function commitHref(sha) { return typeof LINKS.commits[sha] === "string" ? LINKS.commits[sha] : null; }
  function changeTarget(changeRequest) {
    var target = LINKS.changes[text(changeRequest)];
    return isObject(target) && typeof target.label === "string" && typeof target.href === "string" ? target : null;
  }
  function pageTitle() { return typeof VIEW.title === "string" && VIEW.title ? VIEW.title : "Delivery"; }
  function changeLink(changeRequest) {
    var target = changeTarget(changeRequest);
    if (!target) { return el("span", "pr-num", text(changeRequest)); }
    var link = el("a", "issue-link pr-num", target.label);
    link.setAttribute("href", target.href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    var identity = (typeof target.repository === "string" && target.repository ? target.repository + " " : "") + target.label;
    link.title = identity;
    link.setAttribute("aria-label", "Open pull request " + identity);
    link.addEventListener("click", function (event) { event.stopPropagation(); });
    return link;
  }

  function duration(total) {
    if (typeof total !== "number" || !isFinite(total) || total < 0) { return ""; }
    total = Math.round(total);
    if (total < 60) { return total + "s"; }
    var minutes = Math.floor(total / 60);
    if (minutes < 10) { return minutes + "m" + (total % 60 ? " " + (total % 60) + "s" : ""); }
    if (minutes < 60) { return minutes + "m"; }
    var hours = Math.floor(minutes / 60);
    if (hours < 48) { return hours + "h" + (minutes % 60 ? " " + (minutes % 60) + "m" : ""); }
    return Math.floor(hours / 24) + "d";
  }
  function ago(at) {
    var then = seconds(at);
    if (then === null) { return ""; }
    var delta = Date.now() / 1000 - then;
    if (Math.abs(delta) < 45) { return "just now"; }
    return delta > 0 ? duration(delta) + " ago" : "in " + duration(-delta);
  }
  // A past moment as a person says it: "just now", "4 minutes ago", "2 hours ago", "yesterday at 8:47 pm", "on Oct 21".
  function spoken(at) {
    var then = seconds(at);
    if (then === null) { return ""; }
    var delta = Date.now() / 1000 - then;
    if (delta < 45) { return "just now"; }
    var minutes = Math.round(delta / 60);
    if (minutes < 60) { return minutes === 1 ? "a minute ago" : minutes + " minutes ago"; }
    var date = new Date(then * 1000);
    var today = new Date(Date.now());
    var hours = Math.floor(minutes / 60);
    if (date.toDateString() === today.toDateString()) { return hours === 1 ? "an hour ago" : hours + " hours ago"; }
    var yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    var time = ((date.getHours() + 11) % 12 + 1) + ":" + (date.getMinutes() < 10 ? "0" : "") + date.getMinutes() + (date.getHours() < 12 ? " am" : " pm");
    if (date.toDateString() === yesterday.toDateString()) { return "yesterday at " + time; }
    return "on " + MONTHS[date.getMonth()] + " " + date.getDate();
  }
  /** How long something has held, or "" when its start is unreadable or still ahead. */
  function heldFor(at) {
    var then = seconds(at);
    if (then === null || Date.now() / 1000 < then) { return ""; }
    var delta = Date.now() / 1000 - then;
    return delta < 45 ? "just now" : "for " + duration(delta);
  }
  function eventTime(entry) {
    return Object.prototype.hasOwnProperty.call(entry, "occurredAt") ? entry.occurredAt : entry.at;
  }
  function clock(at) {
    var t = typeof at === "string" ? Date.parse(at) : NaN;
    if (isNaN(t)) { return ""; }
    var date = new Date(t);
    var time = ((date.getHours() + 11) % 12 + 1) + ":" + (date.getMinutes() < 10 ? "0" : "") + date.getMinutes() + (date.getHours() < 12 ? " am" : " pm");
    // An entry from another day says which day, so a run that spans days reads in order.
    if (new Date(Date.now()).toDateString() === date.toDateString()) { return time; }
    return MONTHS[date.getMonth()] + " " + date.getDate() + ", " + time;
  }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var IN_CHECKS = table({ syncing: true, synced: true, checking: true, checked: true, merging: true });
  var STAGE_WORDS = table({
    merged: "Landed", stopped: "Stopped, waiting for its lane", queued: "Ready, waiting to be checked",
    pending: "Handed over, waiting for the lead to accept",
    syncing: "Being checked before merge", synced: "Being checked before merge", checking: "Being checked before merge",
    checked: "Checked, about to merge", merging: "Merging", planned: "Not started", building: "Being built", review: "In review"
  });
  var LANE_WORDS = table({ idle: "Standing by", assigned: "Assigned", working: "Building", reviewing: "In review", handed: "Handed over", blocked: "Blocked", done: "Done" });
  var STEP_WORDS = table({ pending: "waiting", running: "running", passed: "passed", failed: "failed", skipped: "skipped", timed_out: "timed out", reused: "reused" });
  var DONE = table({ passed: true, failed: true, skipped: true, timed_out: true, reused: true });
  var GLYPH = table({
    "candidate.merged": ["merge", "●"], "candidate.handed": ["ready", "◐"], "decision.recorded": ["decide", "◆"],
    "blocker.recorded": ["stop", "▲"], "candidate.stopped": ["stop", "▲"], "base.broken": ["stop", "▲"]
  });
  var KIND_WORDS = table({
    "lane.assigned": "Assigned", "review.round": "Review round", "candidate.handed": "Handed over", "check.started": "Check started",
    "check.finished": "Check finished", "check.retried": "Check retried", "check.reused": "Check reused", "candidate.merged": "Landed",
    "candidate.stopped": "Stopped", "base.broken": "Base broken", "base.repaired": "Base repaired", "lease.granted": "Lease granted",
    "lease.released": "Lease released", "release.started": "Release started", "release.finished": "Release finished",
    "finish.met": "Finish line met", "decision.recorded": "Decision", "blocker.recorded": "Blocker", "delivery.closed": "Delivery closed"
  });
  var TABS = [["all", "Everything"], ["decisions", "Decisions"], ["landed", "Landed"], ["problems", "Problems"]];
  function inTab(entry, tab) {
    if (tab === "decisions") { return entry.kind === "decision.recorded"; }
    if (tab === "landed") { return entry.kind === "candidate.merged"; }
    if (tab === "problems") { return entry.kind === "blocker.recorded" || entry.kind === "candidate.stopped" || entry.kind === "base.broken"; }
    return true;
  }

  // The chapters are built once; each draw fills them.
  var CHAPTERS = [["now", "Now"], ["line", "The line"], ["who", "Who"], ["left", "What's left"], ["happened", "What happened"], ["parked", "Parked"]];
  var parts = Object.create(null);
  var rail = el("nav", "rail");
  rail.setAttribute("aria-label", "Chapters");
  app.replaceChildren(rail);
  CHAPTERS.forEach(function (chapter) {
    var section = el("section", "chapter chapter-" + chapter[0]);
    section.id = chapter[0];
    var inner = el("div", "inner");
    section.appendChild(inner);
    parts[chapter[0]] = inner;
    app.appendChild(section);
    var link = el("a");
    link.setAttribute("href", "#" + chapter[0]);
    link.setAttribute("aria-label", chapter[1]);
    add(link, el("span", null, chapter[1]), el("i"));
    rail.appendChild(link);
  });
  // The history page, when the binding declares one: served live at /view/<output>, beside the page as a file.
  if (typeof VIEW.history === "string" && VIEW.history) {
    var historyLink = el("a", "history-link", "Past deliveries and runs");
    historyLink.setAttribute("href", (typeof location === "object" && /^https?:$/.test(location.protocol) ? "/view/" : "") + VIEW.history.split("/").map(encodeURIComponent).join("/"));
    app.appendChild(add(el("footer", "foot"), historyLink));
  }
  if (typeof IntersectionObserver === "function") {
    var watch = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        Array.prototype.forEach.call(rail.querySelectorAll("a"), function (link) {
          link.setAttribute("aria-current", String(link.getAttribute("href") === "#" + entry.target.id));
        });
      });
    }, { rootMargin: "-45% 0px -45% 0px" });
    Array.prototype.forEach.call(app.querySelectorAll(".chapter"), function (section) { watch.observe(section); });
    // The longest entrance: .7s of rise after the last row's .3s delay, with a little to spare.
    var ENTRANCE_MS = 1100;
    // Each chapter settles into place the first time it comes into view; it is fully visible before and after.
    // Once settled it is marked arrived, so a redraw's new rows never play the entrance again.
    var arrive = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        var chapter = entry.target;
        chapter.classList.add("arrive");
        arrive.unobserve(chapter);
        setTimeout(function () { chapter.classList.replace("arrive", "arrived"); }, ENTRANCE_MS);
      });
    }, { threshold: 0.2 });
    Array.prototype.forEach.call(app.querySelectorAll(".chapter"), function (section) { arrive.observe(section); });
  }

  // One card for the whole page, kept outside the chapters so no redraw removes it.
  var card = el("div", "issue-card");
  card.setAttribute("role", "tooltip");
  card.hidden = true;
  app.appendChild(card);
  function ticketCard(id) {
    var doc = shown;
    if (!usable(doc) || !id) { return null; }
    var tickets = isObject(doc.state.tickets) ? doc.state.tickets : {};
    var ticket = Object.prototype.hasOwnProperty.call(tickets, id) && isObject(tickets[id]) ? tickets[id] : null;
    var item = list(doc.state.queue).concat(list(doc.state.planned)).filter(function (entry) { return isObject(entry) && entry.subject === id; })[0] ||
      list(doc.state.followups).filter(function (entry) { return isObject(entry) && (entry.reference === id || entry.id === id); })[0];
    var title = (ticket && text(ticket.title)) || (item && text(item.title)) || "";
    var summary = ticket ? text(ticket.summary) : "";
    return title || summary ? { title: title, summary: summary } : null;
  }
  function showCard(target, id) {
    var found = ticketCard(id);
    if (!found) { return; }
    card.replaceChildren(el("b", null, id));
    if (found.title) { card.appendChild(el("div", "ic-t", found.title)); }
    if (found.summary) { card.appendChild(el("div", "ic-s", found.summary)); }
    card.hidden = false;
    if (typeof target.getBoundingClientRect !== "function" || typeof innerWidth !== "number") { return; }
    var box = target.getBoundingClientRect();
    var width = Math.min(300, innerWidth - 32);
    card.style.left = Math.max(16, Math.min(box.left + box.width / 2 - width / 2, innerWidth - width - 16)) + "px";
    var below = box.bottom + 8, height = card.offsetHeight || 0;
    card.style.top = (below + height > innerHeight - 8 ? box.top - height - 8 : below) + "px";
  }
  function hideCard() { card.hidden = true; }
  if (typeof addEventListener === "function") { addEventListener("scroll", hideCard, { passive: true }); }
  /** A ticket id that shows its card when hovered or focused, and opens the issue when the renderer linked it. */
  function issue(id, label) {
    var href = issueHref(text(id));
    var node = el(href ? "a" : "span", "issue-link", label === undefined ? id : label);
    if (href) {
      node.setAttribute("href", href);
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
      node.addEventListener("click", function (event) { event.stopPropagation(); });
    } else {
      node.setAttribute("tabindex", "0");
    }
    node.setAttribute("data-ticket", text(id));
    node.addEventListener("mouseenter", function () { showCard(node, id); });
    node.addEventListener("focus", function () { showCard(node, id); });
    node.addEventListener("mouseleave", hideCard);
    node.addEventListener("blur", hideCard);
    return node;
  }

  /** Every piece of work on the record, in the order it lands: the queue, then the planned work. */
  function pieces(doc) {
    var queue = list(doc.state.queue).filter(isObject).slice().sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
    var planned = list(doc.state.planned).filter(isObject);
    return queue.map(function (item) { return { item: item, planned: false }; })
      .concat(planned.map(function (item) { return { item: item, planned: true }; }));
  }
  function laneNames(doc) {
    var names = Object.create(null);
    list(doc.state.lanes).forEach(function (lane) { if (isObject(lane) && lane.id) { names[lane.id] = text(lane.title) || text(lane.id); } });
    return names;
  }
  function laneColours(doc, all) {
    var order = [];
    list(doc.state.lanes).forEach(function (lane) { if (isObject(lane) && lane.id && order.indexOf(lane.id) < 0) { order.push(lane.id); } });
    all.forEach(function (piece) { var lane = piece.item.lane || ""; if (order.indexOf(lane) < 0) { order.push(lane); } });
    var colours = Object.create(null);
    order.forEach(function (lane, index) { colours[lane] = lane ? "var(--l" + ((index % 6) + 1) + ")" : "var(--soft)"; });
    return colours;
  }
  function titleOf(doc, item) {
    var tickets = isObject(doc.state.tickets) ? doc.state.tickets : {};
    var ticket = isObject(tickets[item.subject]) ? tickets[item.subject] : null;
    return (ticket && text(ticket.title)) || text(item.title) || text(item.subject);
  }
  function checkingNow(doc) {
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    return isObject(meta.checking) && isObject(meta.checking.now) ? meta.checking.now : null;
  }
  function stopKind(piece, nowSubject) {
    var item = piece.item;
    if (piece.planned) { return item.stage === "planned" ? "later" : item.stage === "building" || item.stage === "review" ? "active" : "unknown"; }
    if (item.stage === "merged") { return "merged"; }
    if (item.stage === "stopped") { return "held"; }
    if (IN_CHECKS[item.stage] || (nowSubject !== null && subjectOf(item) === nowSubject)) { return "now"; }
    if (item.stage !== "queued") { return "unknown"; }
    return item.pending === true ? "ready pending" : "ready";
  }
  function failures(item) {
    return list(item.checks).filter(function (check) { return isObject(check) && (check.state === "failed" || check.state === "timed_out"); })
      .map(function (check) {
        var tests = list(check.failures).map(text).filter(Boolean);
        return text(check.id) + " " + (check.state === "timed_out" ? "timed out" : "failed") + (tests.length ? ": " + tests.join(", ") : "");
      });
  }
  function stageKey(item) { return item.pending === true && item.stage === "queued" ? "pending" : text(item.stage); }
  function stageWords(item) { return STAGE_WORDS[stageKey(item)] || "Stage not recognised"; }
  // Each stage's icon, drawn from SVG elements: a pair of meshed gears that turn while the piece is being built, and for
  // every other stage a still icon as Tabler's outline icons (MIT) draw it. A stage the page does not know gets none.
  var CIRCLE = "M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0";
  var MERGE = ["M7 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0", "M7 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0", "M17 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0", "M7 8l0 8", "M7 8a4 4 0 0 0 4 4h4"];
  var CHECKING = ["M10 10m-7 0a7 7 0 1 0 14 0a7 7 0 1 0 -14 0", "M21 21l-6 -6"];
  var STAGE_ICONS = table({
    planned: ["M8.56 3.69a9 9 0 0 0 -2.92 1.95", "M3.69 8.56a9 9 0 0 0 -.69 3.44", "M3.69 15.44a9 9 0 0 0 1.95 2.92", "M8.56 20.31a9 9 0 0 0 3.44 .69",
      "M15.44 20.31a9 9 0 0 0 2.92 -1.95", "M20.31 15.44a9 9 0 0 0 .69 -3.44", "M20.31 8.56a9 9 0 0 0 -1.95 -2.92", "M15.44 3.69a9 9 0 0 0 -3.44 -.69"],
    review: ["M10 12a2 2 0 1 0 4 0a2 2 0 0 0 -4 0", "M21 12c-2.4 4 -5.4 6 -9 6c-3.6 0 -6.6 -2 -9 -6c2.4 -4 5.4 -6 9 -6c3.6 0 6.6 2 9 6"],
    pending: ["M10 14l11 -11", "M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5"],
    queued: [CIRCLE, "M12 7v5l3 3"],
    syncing: CHECKING, synced: CHECKING, checking: CHECKING,
    checked: [CIRCLE, "M9 12l2 2l4 -4"],
    merging: MERGE, merged: MERGE,
    stopped: [CIRCLE, "M12 8v4", "M12 16h.01"]
  });
  // A gear: a dashed ring for its teeth around a solid rim and an axle hole, so its own group can turn about its centre.
  var GEARS = [
    { className: "gear-big", cx: 9, cy: 9, teeth: 12, outer: 6, rim: 4.6, hole: 1.6 },
    { className: "gear-small", cx: 17.2, cy: 17.2, teeth: 8, outer: 3.9, rim: 2.7, hole: 0.9 },
  ];
  function gears(svg, svgNs) {
    GEARS.forEach(function (gear) {
      var group = document.createElementNS(svgNs, "g");
      group.setAttribute("class", gear.className);
      [[gear.outer, 2.2, 2 * Math.PI * gear.outer / gear.teeth / 2], [gear.rim, 1.6, 0], [gear.hole, 1.4, 0]].forEach(function (ring) {
        var circle = document.createElementNS(svgNs, "circle");
        circle.setAttribute("cx", String(gear.cx));
        circle.setAttribute("cy", String(gear.cy));
        circle.setAttribute("r", String(ring[0]));
        circle.setAttribute("stroke-width", String(ring[1]));
        if (ring[2]) { circle.setAttribute("stroke-dasharray", ring[2].toFixed(3) + " " + ring[2].toFixed(3)); circle.setAttribute("stroke-linecap", "butt"); }
        group.appendChild(circle);
      });
      svg.appendChild(group);
    });
  }
  // An outline icon from its paths, hidden from assistive technology since the words beside it say the same.
  function icon(className, paths) {
    var svgNs = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    paths.forEach(function (d) {
      var path = document.createElementNS(svgNs, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }
  function stageIcon(item) {
    var key = stageKey(item);
    if (key === "building") {
      var svg = icon("d-stage-icon is-building", []);
      gears(svg, "http://www.w3.org/2000/svg");
      return svg;
    }
    return Array.isArray(STAGE_ICONS[key]) ? icon("d-stage-icon is-" + key, STAGE_ICONS[key]) : null;
  }

  /** The pieces handed over and waiting their turn, in landing order: the record's own next list when it has one. */
  function upNext(doc, all, current) {
    var byId = Object.create(null);
    all.forEach(function (piece) { byId[piece.item.subject] = piece.item; });
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var named = isObject(meta.checking) ? list(meta.checking.next).filter(function (subject) { return byId[subject]; }) : [];
    if (named.length) { return named.map(function (subject) { return byId[subject]; }); }
    return all.filter(function (piece) {
      return !piece.planned && piece.item.stage === "queued" && piece.item.pending !== true && (!current || piece.item.subject !== current.subject);
    }).map(function (piece) { return piece.item; });
  }
  /**
   * The pieces checked as one batch right now, or null. The runner journals one batch start per member,
   * one after another with the same summary; that run is the batch while every piece in checks belongs to
   * it. Members that already landed from it stay listed; a member sent back to the queue drops out, and so
   * does one checked again on its own since.
   */
  function batchNow(doc, all) {
    // Only check starts decide: the latest must be a batch start, and the run is the starts before it with the
    // same summary. Other entries between them, such as a lane's note, neither end nor break the run.
    var starts = list(doc.journal).filter(function (entry) { return isObject(entry) && entry.kind === "check.started"; });
    var last = starts[starts.length - 1];
    if (!last || !/ as one batch$/.test(text(last.summary))) { return null; }
    var members = [];
    for (var i = starts.length - 1; i >= 0 && starts[i].summary === last.summary; i -= 1) {
      var subject = starts[i].subject === undefined || starts[i].subject === null ? "" : String(starts[i].subject);
      if (members.indexOf(subject) < 0) { members.unshift(subject); }
    }
    if (members.length < 2) { return null; }
    var queued = all.filter(function (piece) { return !piece.planned; }).map(function (piece) { return piece.item; });
    var inChecks = queued.filter(function (item) { return IN_CHECKS[item.stage]; });
    if (!inChecks.length || !inChecks.every(function (item) { return members.indexOf(subjectOf(item)) >= 0; })) { return null; }
    var batch = members.map(function (subject) {
      return queued.filter(function (item) { return subjectOf(item) === subject && (IN_CHECKS[item.stage] || item.stage === "merged"); })[0];
    }).filter(Boolean);
    return batch.length > 1 ? batch : null;
  }
  function journalFor(doc, subject, kinds) {
    return list(doc.journal).filter(function (entry) { return isObject(entry) && entry.subject === subject && (!kinds || kinds[entry.kind]); });
  }

  /* 1. Now */
  function drawNow(doc, all) {
    var inner = parts.now;
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var landed = all.filter(function (piece) { return !piece.planned && piece.item.stage === "merged"; }).length;
    var title = el("h1", "title");
    if (!all.length) { add(title, "Nothing on the line yet."); }
    else if (landed === all.length) { add(title, "Every piece has landed."); }
    else { add(title, el("b", null, landed + " of " + all.length), " pieces have landed."); }
    var kicker = add(el("div", "kicker"), el("span", "headline", text(meta.headline) || pageTitle()));
    // The tracker item the delivery's work is listed under, linked when the renderer linked it.
    if (text(meta.trackerParent)) { add(kicker, " · ", issue(text(meta.trackerParent))); }
    var nodes = [kicker, title];
    var status = sayLine(doc, all);
    if (status) { nodes.push(el("p", "say", status)); }
    var panel = checkingPanel(doc, all);
    if (panel) { nodes.push(panel); }
    var needs = attention(doc);
    if (needs) { nodes.push(needs); }
    nodes.push(journey(doc, all, landed));
    nodes.push(since(meta));
    inner.replaceChildren.apply(inner, nodes);
  }

  /**
   * The sentence under the headline. A lead's override is shown whole; the derived status repeats what the
   * panels below already say, so the page says what landed last instead.
   */
  function sayLine(doc, all) {
    var status = isObject(doc.status) ? doc.status : {};
    if (status.source === "override" && text(status.text)) { return text(status.text); }
    var merges = list(doc.journal).filter(function (entry) { return isObject(entry) && entry.kind === "candidate.merged"; });
    var landed = merges.filter(function (entry) { return seconds(eventTime(entry)) !== null; })
      .sort(function (a, b) { return seconds(eventTime(a)) - seconds(eventTime(b)) || a.id - b.id; }).pop();
    if (landed) {
      var item = all.filter(function (piece) { return piece.item.subject === landed.subject; })[0];
      return "Last landed: " + (item ? titleOf(doc, item.item) : text(landed.subject)) + ", " + ago(eventTime(landed)) + ".";
    }
    if (merges.length) { return "Work has landed; merge time unknown."; }
    return all.length ? "Nothing has landed yet." : "";
  }

  function checkingPanel(doc, all) {
    var current = checkingNow(doc);
    var byId = Object.create(null);
    all.forEach(function (piece) { byId[piece.item.subject] = piece.item; });
    var nextNames = upNext(doc, all, current).map(function (item) { return titleOf(doc, item); });
    var box = el("div", "checking");
    box.setAttribute("aria-live", "polite");
    if (!current) {
      var waiting = all.filter(function (piece) { return !piece.planned && piece.item.stage === "queued" && piece.item.pending === true; }).length;
      if (!nextNames.length && !waiting) { return null; }
      if (nextNames.length) {
        add(box, add(el("div", "ck-h"), el("span", "ck-dot"), nextNames.length === 1 ? "Ready to be checked" : nextNames.length + " pieces ready to be checked"));
        add(box, el("div", "ck-t", nextNames.join(" · ")));
        add(box, el("div", "ck-s", "Handed over; each is checked and merged in turn, in this order."));
      }
      if (waiting) { add(box, el("div", "ck-s", waiting + (waiting === 1 ? " more is" : " more are") + " waiting for the lead to accept the hand-over.")); }
      return box;
    }
    var batch = batchNow(doc, all);
    if (batch) {
      var landedIn = batch.filter(function (item) { return item.stage === "merged"; }).length;
      add(box, add(el("div", "ck-h"), el("span", "ck-dot"), landedIn ? "Landing together · " + landedIn + " of " + batch.length + " in" : "Being checked together · " + batch.length + " pieces"));
      var members = el("ol", "ck-members");
      batch.forEach(function (item) {
        var li = add(el("li", item.stage === "merged" ? "in" : ""), item.changeRequest ? changeLink(item.changeRequest) : null, " " + titleOf(doc, item));
        if (item.stage === "merged") { li.appendChild(el("span", "ck-in", "landed")); }
        members.appendChild(li);
      });
      add(box, members);
    } else {
      add(box, add(el("div", "ck-h"), el("span", "ck-dot"), "Being checked now"));
      add(box, el("div", "ck-t", byId[current.subject] ? titleOf(doc, byId[current.subject]) : text(current.title) || text(current.subject)));
    }
    var steps = list(current.steps).filter(function (step) { return isObject(step) && step.label !== "selecting checks"; });
    if (steps.length) {
      var done = steps.filter(function (step) { return DONE[step.state]; }).length;
      var failedSteps = steps.filter(function (step) { return step.state === "failed" || step.state === "timed_out"; });
      var failed = failedSteps.length;
      // Once a check fails, the piece cannot merge from this run, whatever is still to run.
      if (failed) {
        var left = steps.length - done;
        // A failed batch still lands the pieces ahead of the one that broke it, so it only won't land as one.
        add(box, add(el("div", "ck-verdict"), el("b", null, batch ? "The batch won't land as one." : "Won't merge from this run."),
          el("span", null, " " + failed + " failed" + (left ? " · " + left + " still to run" : "") + (batch ? " · the pieces ahead of the one that broke it can still land" : ""))));
      }
      var line = null;
      if (steps.length <= 12 && !failed) {
        var row = el("div", "ck-steps");
        steps.forEach(function (step) {
          var mark = step.state === "passed" || step.state === "reused" ? "✓ " : step.state === "failed" || step.state === "timed_out" ? "✕ " : "";
          row.appendChild(el("span", "ck-step " + (STEP_WORDS[step.state] ? step.state : "pending"), mark + text(step.label)));
        });
        add(box, row);
      } else if ((line = runLine(current, steps))) {
        add(box, line);
      } else {
        var bar = el("div", "ck-bar");
        steps.forEach(function (step) { bar.appendChild(el("span", STEP_WORDS[step.state] ? step.state : "pending")); });
        add(box, bar);
      }
      if (failed) { add(box, failedChecks(doc, batch ? batch.map(subjectOf) : [current.subject === undefined || current.subject === null ? "" : String(current.subject)], current, failedSteps)); }
      steps.filter(function (step) { return step.state === "running"; }).forEach(function (step) {
        var began = seconds(step.startedAt);
        var spent = began === null ? null : Date.now() / 1000 - began;
        var words = "Running " + text(step.label);
        var over = false;
        if (spent !== null && spent >= 0) {
          // Without a usual time to compare against, the time so far is only a time, never an alarm.
          words += " · " + duration(spent) + (typeof step.expectedSeconds === "number" ? " of about " + duration(step.expectedSeconds) : " so far");
          over = typeof step.expectedSeconds === "number" && spent > step.expectedSeconds;
        }
        add(box, el("div", "ck-run" + (over ? " over" : ""), words + (over ? ", longer than usual" : "")));
      });
      var running = steps.filter(function (step) { return step.state === "running"; }).length;
      var tally = function (state) { return steps.filter(function (step) { return step.state === state; }).length; };
      var counts = failed
        ? [tally("passed") + " passed", tally("reused") ? tally("reused") + " reused" : "", tally("skipped") ? tally("skipped") + " skipped" : "", failed + " failed",
          running ? running + " running" : "", steps.length - done - running ? steps.length - done - running + " waiting" : ""].filter(Boolean).join(", ")
        : done + " of " + steps.length + " checks done";
      add(box, el("div", "ck-s", (batch ? "One test run for all of them; each lands only if it matches what was tested · " : "") + counts +
        (ago(current.startedAt) ? " · started " + ago(current.startedAt) : "")));
    } else if (ago(current.startedAt)) {
      add(box, el("div", "ck-s", "Started " + ago(current.startedAt)));
    }
    if (nextNames.length) { add(box, el("div", "ck-s", "Up next: " + nextNames.join(" · "))); }
    return box;
  }

  var SVG = "http://www.w3.org/2000/svg";
  function shape(parent, tag, attributes, words) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attributes).forEach(function (name) { node.setAttribute(name, String(attributes[name])); });
    if (words !== undefined) { node.textContent = words; }
    parent.appendChild(node);
    return node;
  }
  /**
   * The check run as a stretch of line on the clock: each check runs from where it started to where it
   * finished, so a long one is a long stretch, and a stem above its stop rises with its time. Failed stops
   * are numbered as the list below them; the running one sits at now, and the waiting ones trail off dotted.
   * Null when the record has no check times to place.
   */
  function runLine(current, steps) {
    var start = seconds(current.startedAt);
    var now = Date.now() / 1000;
    var timed = steps.filter(function (step) { return seconds(step.startedAt) !== null; });
    if (start === null || now <= start || !timed.length) { return null; }
    var waiting = steps.filter(function (step) { return !DONE[step.state] && step.state !== "running"; }).length;
    // Drawn at the width it is shown at, so its words stay their size on a phone.
    var width = Math.round(Math.max(300, Math.min(560, typeof innerWidth === "number" && innerWidth > 0 ? innerWidth - 80 : 560)));
    var left = 8, right = width - (waiting ? Math.min(110, Math.round(width * 0.3)) : 16), y = 52;
    var x = function (t) { return left + Math.max(0, Math.min(1, (t - start) / (now - start))) * (right - left); };
    var kind = function (step) { return step.state === "failed" || step.state === "timed_out" ? "failed" : step.state === "running" ? "running" : "passed"; };
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "run-line");
    svg.setAttribute("viewBox", "0 0 " + width + " 92");
    svg.setAttribute("role", "img");
    var first = Math.min.apply(null, timed.map(function (step) { return seconds(step.startedAt); }));
    shape(svg, "line", { class: "rl-lead", x1: left, x2: x(first), y1: y, y2: y });
    var spans = timed.map(function (step) {
      var from = seconds(step.startedAt), to = step.state === "running" ? now : seconds(step.finishedAt);
      // A clock behind the runner's can put an end before its start, or a start after now: such a check is a point.
      from = Math.min(from, now);
      return { step: step, kind: kind(step), from: from, to: to === null ? from : Math.max(from, to) };
    });
    var longest = Math.max.apply(null, spans.map(function (span) { return span.to - span.from; }).concat([1]));
    spans.forEach(function (span) { shape(svg, "line", { class: "rl-seg " + span.kind, x1: x(span.from), x2: Math.max(x(span.from), x(span.to)), y1: y, y2: y }); });
    // Stems and their times: tallest for the longest check; a time is written only where it has room.
    var lastLabel = -100;
    spans.slice().sort(function (a, b) { return a.to - b.to; }).forEach(function (span) {
      var took = span.to - span.from;
      var height = 4 + 26 * Math.sqrt(took / longest);
      var at = x(span.to);
      shape(svg, "line", { class: "rl-stem " + span.kind, x1: at, x2: at, y1: y - 8, y2: y - 8 - height });
      var worth = span.kind !== "passed" || took >= 300;
      if (worth && at - lastLabel > 46) {
        shape(svg, "text", { class: "rl-time " + span.kind, x: at, y: y - 12 - height, "text-anchor": "middle" }, duration(took) + (span.kind === "running" ? " so far" : ""));
        lastLabel = at;
      }
    });
    // Numbered by place among all the failed checks, as the list below is, even if one has no time to draw.
    var failures = steps.filter(function (step) { return kind(step) === "failed"; });
    var lastNumber = -100;
    spans.forEach(function (span) {
      var at = x(span.to);
      if (span.kind === "failed") {
        var number = failures.indexOf(span.step) + 1;
        shape(svg, "circle", { class: "rl-stop failed", cx: at, cy: y, r: 5.5 });
        // Failures close together keep their numbers apart, each still under or just after its stop.
        var place = Math.min(Math.max(at, lastNumber + 11), width - 6);
        shape(svg, "text", { class: "rl-num", x: place, y: y + 20, "text-anchor": "middle" }, String(number));
        lastNumber = place;
      } else if (span.kind === "running") {
        shape(svg, "circle", { class: "rl-ring", cx: at, cy: y, r: 9 });
        shape(svg, "circle", { class: "rl-stop running", cx: at, cy: y, r: 5 });
      } else {
        shape(svg, "circle", { class: "rl-stop passed", cx: at, cy: y, r: 2.5 });
      }
    });
    if (waiting) {
      shape(svg, "line", { class: "rl-tail", x1: right + 16, x2: width - 6, y1: y, y2: y });
      var dots = Math.min(4, waiting), gap = (width - 6 - (right + 24)) / Math.max(1, dots);
      for (var i = 0; i < dots; i += 1) { shape(svg, "circle", { class: "rl-wait", cx: right + 24 + i * gap, cy: y, r: 2.5 }); }
      shape(svg, "text", { class: "rl-tick", x: (right + width) / 2 + 4, y: y - 14, "text-anchor": "middle" }, waiting + " waiting");
    }
    shape(svg, "text", { class: "rl-tick", x: left, y: 89 }, clock(current.startedAt));
    shape(svg, "text", { class: "rl-tick", x: right, y: 89, "text-anchor": "end" }, "now");
    var failedCount = spans.filter(function (span) { return span.kind === "failed"; }).length;
    svg.setAttribute("aria-label", "Check run from " + clock(current.startedAt) + " to now: " + timed.length + " started" +
      (failedCount ? ", " + failedCount + " failed" : "") + (waiting ? ", " + waiting + " waiting" : ""));
    return svg;
  }

  /**
   * The failed checks of the piece being checked, first and apart from the rest: each with its log and the
   * failing tests the record names, and the lead's notes on it beside it. Notes on the piece that name no
   * failed check follow the list.
   */
  function failedChecks(doc, subjects, current, failedSteps) {
    var records = Object.create(null);
    list(doc.state.queue).filter(function (entry) { return isObject(entry) && subjects.indexOf(subjectOf(entry)) >= 0; }).forEach(function (item) {
      list(item.checks).forEach(function (check) { if (isObject(check) && typeof check.id === "string" && !records[check.id]) { records[check.id] = check; } });
    });
    var startedAt = seconds(current.startedAt);
    var notes = list(doc.journal).filter(function (entry) {
      if (!isObject(entry) || subjects.indexOf(String(entry.subject)) < 0 || (entry.kind !== "blocker.recorded" && entry.kind !== "decision.recorded")) { return false; }
      var at = seconds(entry.at);
      // A note from before this check run is about an earlier one; an undated note counts only when the run's start is unknown.
      return startedAt === null || (at !== null && at >= startedAt);
    });
    var used = [];
    // The words of a note, without the punctuation around them, so a check id matches only as a whole word.
    function names(summary) {
      var words = text(summary).split(/\s+/);
      // Each word as written and without the punctuation around it, so a label that itself ends in punctuation still matches.
      return words.concat(words.map(function (word) { return word.replace(/^[("'\x60\[]+/, "").replace(/[)"'\x60\],;:.!?]+$/, ""); }));
    }
    function note(entry) {
      used.push(entry);
      var glyph = entry.kind === "blocker.recorded" ? ["stop", "▲"] : ["decide", "◆"];
      return add(el("div", "ck-note"), el("span", "g " + glyph[0], glyph[1]), " " + text(entry.summary));
    }
    var ul = el("ul", "ck-fails");
    failedSteps.forEach(function (step) {
      var label = text(step.label);
      var record = records[label] || {};
      // Laid out like the Needs attention list: a small mark, the check, then what is known about it.
      // Numbered as its stop on the run line above.
      var mark = el("span", "g num", String(failedSteps.indexOf(step) + 1));
      mark.setAttribute("aria-hidden", "true");
      var body = add(el("div"), add(el("div", "ck-f"), el("span", "sr", step.state === "timed_out" ? "Timed out: " : "Failed: "), label));
      var tests = list(record.failures).map(text).filter(Boolean);
      if (tests.length) { body.appendChild(el("div", "ck-d", "Failing: " + tests.join(", "))); }
      if (text(record.detail)) { body.appendChild(el("div", "ck-d", text(record.detail))); }
      notes.filter(function (entry) { return label && names(entry.summary).indexOf(label) >= 0; }).forEach(function (entry) { body.appendChild(note(entry)); });
      if (text(record.log)) {
        var log = el("div", "ck-log", "Log: " + text(record.log));
        body.appendChild(log);
      }
      ul.appendChild(add(el("li", step.state === "timed_out" ? "timed_out" : "failed"), mark, body));
    });
    var box = add(el("div", "ck-failures"), ul);
    var general = notes.filter(function (entry) { return used.indexOf(entry) < 0; });
    if (general.length) {
      var more = el("div", "ck-more");
      general.forEach(function (entry) { more.appendChild(note(entry)); });
      box.appendChild(more);
    }
    return box;
  }

  function attention(doc) {
    var state = doc.state, meta = isObject(state.meta) ? state.meta : {};
    var names = laneNames(doc);
    var rows = [];
    function row(tone, title, why) {
      var li = el("li");
      add(li, el("span", "g" + (tone === "wait" ? " wait" : ""), tone === "wait" ? "◇" : "▲"), add(el("div"), el("div", "a1", title), why ? el("div", "a2", why) : null));
      rows.push(li);
    }
    if (isObject(meta.hold)) { row("stop", "Serving is paused", text(meta.hold.reason)); }
    list(state.queue).filter(function (item) { return isObject(item) && item.stage === "stopped"; }).forEach(function (item) {
      var why = failures(item);
      var stopped = journalFor(doc, item.subject, { "candidate.stopped": true }).pop();
      row("stop", titleOf(doc, item) + " stopped", why.length ? why.join("; ") : stopped ? text(stopped.summary) : "No reason recorded.");
    });
    var baseEvents = list(doc.journal).filter(function (entry) { return isObject(entry) && (entry.kind === "base.broken" || entry.kind === "base.repaired"); });
    var lastBase = baseEvents[baseEvents.length - 1];
    if (lastBase && lastBase.kind === "base.broken" && !isObject(meta.hold)) { row("stop", "The main line is broken", text(lastBase.summary) || "No reason recorded."); }
    list(state.lanes).filter(function (lane) { return isObject(lane) && lane.state === "blocked"; }).forEach(function (lane) {
      row("wait", (names[lane.id] || text(lane.id)) + " is blocked", lane.waitingOn ? "Waiting on " + text(lane.waitingOn) : "No reason recorded.");
    });
    list(meta.sourceReports).forEach(function (report) {
      if (isObject(report)) { row("wait", report.subject ? text(report.subject) : "For the lead", text(report.note)); }
    });
    var sources = isObject(meta.sources) ? meta.sources : {};
    Object.keys(sources).forEach(function (name) {
      var source = sources[name];
      if (isObject(source) && source.status === "stale") {
        row("wait", "The " + name + " is not answering", (text(source.reason) ? text(source.reason) + ". " : "") +
          (source.lastReadAt ? "Last read " + ago(source.lastReadAt) + "." : "Never read."));
      }
    });
    if (!rows.length) { return null; }
    var box = el("div", "attention");
    var ul = el("ul");
    rows.forEach(function (li) { ul.appendChild(li); });
    return add(box, el("div", "ah", "Needs attention"), ul);
  }

  function journey(doc, all, landed) {
    var finish = list(doc.state.finish).filter(isObject);
    var met = finish.filter(function (line) { return line.met === true; }).length;
    var total = all.length + finish.length;
    var progress = total ? (landed + met) / total : 0;
    var box = el("div", "journey");
    box.setAttribute("aria-hidden", "true");
    add(box, el("div", "track"));
    var done = el("div", "done");
    var mark = el("div", "mark");
    // The bar grows from where it last stood, so a landing is seen moving rather than swapped in.
    var from = prev.progress === null || reduced() ? progress : prev.progress;
    done.style.width = from * 100 + "%";
    mark.style.left = from * 100 + "%";
    add(box, done, mark);
    if (from !== progress) {
      later(function () { done.style.width = progress * 100 + "%"; mark.style.left = progress * 100 + "%"; replay(mark, "moved"); });
    }
    prev.progress = progress;
    // Without finish lines the pieces landing is the end of the journey, so it takes the end's place.
    var stops = finish.length
      ? [["Start", 0, "start"], ["Pieces landed", total ? all.length / total : 1, "pieces"], ["Finish line met", 1, "end"]]
      : [["Start", 0, "start"], ["Pieces landed", 1, "end"]];
    stops.forEach(function (stop) {
      var label = el("div", "stop " + stop[2], stop[0]);
      label.style.left = stop[2] === "pieces" ? "clamp(55px, " + stop[1] * 100 + "%, calc(100% - 55px))" : stop[1] * 100 + "%";
      var tick = el("div", "tick");
      tick.style.left = stop[1] * 100 + "%";
      add(box, label, tick);
    });
    return box;
  }

  function since(meta) {
    var updated = seconds(meta.updatedAt);
    var stale = updated !== null && Date.now() / 1000 - updated > 90 * 60;
    var cls = connection === "live" || connection === "connected" ? "live" : connection === "saved" ? "" : "down";
    var box = el("div", "since " + (stale ? "stale" : cls));
    var words = { live: "Live", connected: "Connected, waiting for updates", down: "Reconnecting. Showing the last update.",
      unreadable: "Live record unreadable. Showing the saved view.", failed: "Live, but the latest update could not be shown", saved: "Saved view" };
    var bits = [words[connection] || words.saved];
    if (updated !== null) { bits.push(stale ? "no update for " + ago(meta.updatedAt).replace(" ago", "") : "updated " + ago(meta.updatedAt)); }
    if (meta.baseHead) { bits.push("base " + short(meta.baseHead)); }
    return add(box, el("i"), bits.join(" · "));
  }

  /* 2. The line */
  function drawLine(doc, all) {
    var inner = parts.line;
    var names = laneNames(doc);
    var colours = laneColours(doc, all);
    var current = checkingNow(doc);
    var nowSubject = current && current.subject !== undefined && current.subject !== null ? String(current.subject) : null;
    var nodes = [el("div", "kicker", "The line"), el("h2", "title", "Every piece of work merges into one main line."),
      el("p", "say", "Each row is one lane's work, in the order it lands. Point at or tap a stop to see what it carries.")];
    if (!all.length) {
      nodes.push(el("p", "waiting", "No work on the line yet."));
      inner.replaceChildren.apply(inner, nodes);
      return;
    }
    var lanes = [];
    all.forEach(function (piece) { var lane = piece.item.lane || ""; if (lanes.indexOf(lane) < 0) { lanes.push(lane); } });
    var box = el("div", "lines");
    var stages = Object.create(null);
    var buttons = [];
    lanes.forEach(function (lane) {
      var items = all.filter(function (piece) { return (piece.item.lane || "") === lane; });
      var row = el("div", "line");
      row.style.setProperty("--c", colours[lane]);
      var landedHere = items.filter(function (piece) { return !piece.planned && piece.item.stage === "merged"; }).length;
      add(row, add(el("div", "name"), el("b", null, lane ? names[lane] || lane : "Not yet assigned"), landedHere + " of " + items.length + " landed"));
      var stops = el("div", "stops");
      var merged = function (k) { return k >= 0 && k < items.length && !items[k].planned && items[k].item.stage === "merged"; };
      items.forEach(function (piece, i) {
        var kind = stopKind(piece, nowSubject);
        var key = subjectOf(piece.item);
        stages[key] = kind;
        var linkLeft = i === 0 || (merged(i) && merged(i - 1));
        var linkRight = merged(i) && merged(i + 1);
        var landing = prev.stages && prev.stages[key] && prev.stages[key] !== "merged" && kind === "merged" && !reduced();
        var seg = el("div", "seg" + (linkLeft ? " link-left" : "") + (linkRight ? " link-right" : "") + (kind === "active" && !linkLeft ? " incoming" : "") +
          (landing && (linkLeft || linkRight) ? " drawing" : ""));
        var button = el("button", "stop-btn " + kind + (landing ? " landing" : ""));
        button.type = "button";
        button.setAttribute("data-subject", key);
        button.setAttribute("aria-label", titleOf(doc, piece.item) + ", " + stageWords(piece.item));
        button.setAttribute("aria-pressed", String(selected === key));
        var show = function () { selected = key; showDetail(doc, piece, colours, nowSubject); };
        button.addEventListener("click", show);
        button.addEventListener("mouseenter", show);
        button.addEventListener("focus", show);
        buttons.push(button);
        // The change request sits under the ticket once one is open, and opens it.
        add(seg, button, add(el("div", "stop-label"), issue(key), piece.item.changeRequest ? changeLink(piece.item.changeRequest) : null));
        stops.appendChild(seg);
      });
      add(row, stops);
      box.appendChild(row);
    });
    var detail = el("div", "detail");
    detail.id = "detail";
    detail.setAttribute("aria-live", "polite");
    var legend = el("div", "line-legend");
    [["f", "Landed"], ["n", "Being checked"], ["r", "Ready to check"], ["a", "In progress"], ["d", "Not started"], ["p", "Waiting for the lead to accept"], ["h", "Stopped"], ["link", "Both landed"]].forEach(function (entry) {
      legend.appendChild(add(el("span"), el("i", entry[0]), entry[1]));
    });
    nodes.push(box, detail, legend);
    inner.replaceChildren.apply(inner, nodes);
    prev.stages = stages;
    var pick = all.filter(function (piece) { return subjectOf(piece.item) === selected; })[0] ||
      all.filter(function (piece) { return stopKind(piece, nowSubject) === "now"; })[0] ||
      all.filter(function (piece) { return stopKind(piece, nowSubject) === "held"; })[0] ||
      all.filter(function (piece) { return stopKind(piece, nowSubject) !== "merged"; })[0] || all[0];
    if (pick) { showDetail(doc, pick, colours, nowSubject); }
  }

  function stageTime(doc, item) {
    var stats = isObject(doc.stats) && isObject(doc.stats[item.subject]) ? doc.stats[item.subject] : {};
    var own = list(doc.journal).filter(function (entry) { return isObject(entry) && entry.subject === item.subject; });
    var handedIndex = -1;
    own.forEach(function (entry, index) { if (entry.kind === "candidate.handed") { handedIndex = index; } });
    var attempt = own.slice(handedIndex + 1);
    var stoppedIndex = attempt.map(function (entry) { return entry.kind; }).lastIndexOf("candidate.stopped");
    var checks = attempt.slice(stoppedIndex + 1);
    function last(kind, rows) { return (rows || attempt).filter(function (entry) { return entry.kind === kind; }).pop(); }
    var event, label, basis, at = null;
    if (item.stage === "building") {
      label = "Started"; basis = "Recorded work assignment"; event = last("lane.assigned");
      at = event ? event.at : (handedIndex < 0 ? stats.assignedAt : null);
    }
    else if (item.stage === "review") {
      label = "In review since"; basis = "Recorded review round start";
      event = last("review.round"); at = event && isObject(event.review) ? event.review.startedAt : null;
    } else if (item.stage === "queued") {
      label = "Handed over"; basis = "Recorded hand-off"; event = handedIndex >= 0 ? own[handedIndex] : null;
    } else if (item.stage === "checking") {
      label = "Checking since"; basis = "Recorded check start"; event = last("check.started", checks);
    } else if (item.stage === "checked") {
      label = "Checked"; basis = "Recorded check finish";
      var start = checks.map(function (entry) { return entry.kind; }).lastIndexOf("check.started");
      event = start >= 0 ? last("check.finished", checks.slice(start + 1)) : null;
    } else if (item.stage === "stopped") {
      label = "Stopped"; basis = "Recorded stop"; event = last("candidate.stopped");
    } else if (item.stage === "merged") {
      label = "Landed"; basis = "Merge occurrence"; event = last("candidate.merged");
      if (!event) {
        event = own.filter(function (entry) { return entry.kind === "candidate.merged" && isObject(entry.recovery)
          && entry.recovery.head === (item.syncedHead || item.head) && entry.recovery.changeRequest === (item.changeRequest || null); }).pop();
      }
    } else if (item.stage === "merging" || item.stage === "syncing" || item.stage === "synced") {
      label = "Time"; basis = "No durable transition time recorded";
    } else { return null; }
    if (event && item.stage !== "review") { at = eventTime(event); }
    return { label: label, basis: basis, at: at };
  }

  function showDetail(doc, piece, colours, nowSubject) {
    var detail = document.getElementById("detail");
    if (!detail) { return; }
    var item = piece.item;
    detail.style.setProperty("--c", colours[item.lane || ""] || "var(--soft)");
    var subject = subjectOf(item);
    var switched = prev.selected !== subject;
    var from = runHeldHeight(detail);
    if (switched && !reduced()) { replay(detail, "swap"); }
    prev.selected = subject;
    // The lane that built the piece labels the panel, by the lane's own name: a lane's title follows its current work, so
    // it would name another piece. Under the title, the piece's ticket and change request, then where it is and when it landed.
    var lane = text(item.lane);
    var owner = el("div", "d-owner" + (lane ? "" : " is-none"), lane ? (lane === "lead" ? "Lead" : lane.replace(/^lane-/, "").replace(/^./, function (first) { return first.toUpperCase(); })) + " lane" : "Not yet assigned");
    var line = el("div", "d2 d-meta");
    add(line, issue(subject));
    if (item.changeRequest) { add(line, " · ", changeLink(item.changeRequest)); }
    var timing = stageTime(doc, item);
    var stage = el("span", "d-stage");
    var icon = stageIcon(item);
    if (icon) { stage.appendChild(icon); }
    add(stage, stageWords(item));
    if (timing) {
      var relative = spoken(timing.at);
      var when = el("span", "d-stage-time", (item.stage === "merged" ? (relative ? " " + relative : " · time unknown")
        : " · " + timing.label + (relative ? " " + relative : (timing.label === "Time" ? " unknown" : " · time unknown"))));
      var exact = timing.basis + ": " + (relative ? timing.at : "unknown");
      when.title = exact;
      when.setAttribute("aria-label", exact);
      stage.appendChild(when);
    }
    add(line, " · ", stage);
    var nodes = [owner, el("div", "d1", titleOf(doc, item)), line];
    var batch = item.stage !== "merged" && batchNow(doc, pieces(doc));
    if (batch && batch.some(function (member) { return member.subject === item.subject; })) {
      nodes.push(el("div", "d-batch", "Checked together with " + batch.filter(function (member) { return member.subject !== item.subject; })
        .map(function (member) { return text(member.subject); }).join(", ") + "; lands in order once the shared test run passes."));
    }
    var tickets = isObject(doc.state.tickets) ? doc.state.tickets : {};
    if (isObject(tickets[item.subject]) && text(tickets[item.subject].summary)) { nodes.push(el("div", "d2", text(tickets[item.subject].summary))); }
    if (item.stage === "stopped") { failures(item).forEach(function (why) { nodes.push(el("div", "d3", why)); }); }
    var leadInputs = list(item.leadInputs).map(text).filter(Boolean);
    if (leadInputs.length) { nodes.push(el("div", "d2", "Needs the lead first: " + leadInputs.join(", "))); }
    var estimate = isObject(doc.estimates) && isObject(doc.estimates[item.subject]) ? doc.estimates[item.subject] : null;
    if (estimate && item.stage === "queued") {
      var expected = seconds(estimate.expectedStart);
      var ahead = expected === null ? null : expected - Date.now() / 1000;
      nodes.push(el("div", "d2", "Position " + estimate.position + " in line · " +
        (ahead === null ? "start time unknown" : ahead > 60 ? "starts around " + clock(estimate.expectedStart) + " (estimate)" : "starts any moment now (estimate)")));
    }
    var run = runFor(doc, item);
    if (run) { nodes.push(run); }
    var timeline = timelineFor(doc, item);
    if (timeline) { nodes.push(timeline); }
    var history = historyFor(doc, item);
    if (history) { nodes.push(history); }
    detail.replaceChildren.apply(detail, nodes);
    // A new piece's panel grows or shrinks to its height smoothly; a live redraw of the same piece does not move.
    if (switched && from) { runEaseHeight(detail, from); }
    Array.prototype.forEach.call(document.querySelectorAll(".stop-btn"), function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-subject") === subject));
    });
  }

  // The run that built the stop, as the product's run view summarized it when the page was rendered.
  // A round the view gives no lens verdicts for takes them from the record's own review.round entry.
  function runFor(doc, item) {
    if (typeof item.runId !== "string" || item.runId === "") {
      var native = Object.prototype.hasOwnProperty.call(nativeReviews, item.subject) ? nativeReviews[item.subject] : null;
      var events = journalFor(doc, item.subject, { "review.round": true, "review.evidence": true });
      var boundary = events.reduce(function (last, event) { return Math.max(last, event.id || 0); }, 0);
      var evidence = events.filter(function (event) { return event.kind === "review.evidence"; }).slice(-1)[0];
      var observation = isObject(doc.reviewEvidence) ? doc.reviewEvidence[item.subject] : undefined;
      if (!native && !events.length) { return null; }
      if (!isObject(native) || native.unavailable === true || native.candidateRef !== item.head
          || native.journalId !== boundary
          || (evidence && (!isObject(evidence.reviewArtifact) || evidence.reviewArtifact.digest !== native.digest))
          || (isObject(doc.reviewEvidence) && (!isObject(observation) || observation.unavailable === true || observation.digest !== native.digest))) {
        return el("div", "d-run-none", "Review evidence unavailable or stale for this candidate.");
      }
      var nativeBox = el("div", "d-run");
      nativeBox.appendChild(runSummaryNode(native, { lenses: VIEW.lenses }));
      return nativeBox;
    }
    if (VIEW.runs !== true) { return null; }
    var summary = Object.prototype.hasOwnProperty.call(runs, item.runId) ? runs[item.runId] : null;
    if (!isObject(summary) || summary.unavailable === true) { return el("div", "d-run-none", "Run " + item.runId + " unavailable."); }
    var journaled = {};
    journalFor(doc, item.subject, { "review.round": true }).forEach(function (entry) {
      if (isObject(entry.review) && isObject(entry.review.verdicts)) { journaled[entry.review.round] = entry.review.verdicts; }
    });
    var rounds = list(summary.roundDetail).map(function (round) {
      if (!isObject(round) || (isObject(round.verdicts) && Object.keys(round.verdicts).length) || !journaled[round.round]) { return round; }
      return Object.assign({}, round, { verdicts: journaled[round.round] });
    });
    var box = el("div", "d-run");
    box.appendChild(runSummaryNode(Object.assign({}, summary, { roundDetail: rounds }), { commitHref: commitHref, lenses: VIEW.lenses }));
    return box;
  }

  // A push redraws at once; the runs come from the view the server renders after it.
  var runsAsked = null;
  function refreshRuns() {
    if (typeof fetch !== "function" || typeof location !== "object" || !/^https?:$/.test(location.protocol)) { return; }
    clearTimeout(runsAsked);
    runsAsked = setTimeout(function () {
      fetch(location.href, { cache: "no-store" }).then(function (answer) { return answer.text(); }).then(function (page) {
        var found = /<script type="application\/json" id="delivery-runs">([^<]*)<\/script>/.exec(page);
        var next = found ? JSON.parse(found[1]) : null;
        var linked = /<script type="application\/json" id="delivery-links">([^<]*)<\/script>/.exec(page);
        if (linked) { readLinks(JSON.parse(linked[1])); }
        var reviews = /<script type="application\/json" id="delivery-reviews">([^<]*)<\/script>/.exec(page);
        if (reviews) { nativeReviews = JSON.parse(reviews[1]); }
        if (isObject(next)) { runs = next; safely(lastGood || shown); }
      }).catch(function () { /* the runs already shown stay */ });
    }, 3000);
  }

  function timelineFor(doc, item) {
    var stats = isObject(doc.stats) && isObject(doc.stats[item.subject]) ? doc.stats[item.subject] : null;
    if (!stats) { return null; }
    var rounds = typeof stats.rounds === "number" ? stats.rounds : null;
    var partsOf = [["Building", stats.buildSeconds, "b"], ["Review", stats.reviewSeconds, "r"], ["Waiting", stats.waitSeconds, "w"], ["Check to merge", stats.checkToMergeSeconds, "c"]]
      .filter(function (part) { return typeof part[1] === "number" && part[1] > 0; });
    var box = el("div", "tl");
    if (partsOf.length) {
      var sum = partsOf.reduce(function (total, part) { return total + part[1]; }, 0) || 1;
      var bar = el("div", "tl-bar");
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", partsOf.map(function (part) { return part[0] + " " + duration(part[1]); }).join(", "));
      partsOf.forEach(function (part) {
        var span = el("span", "tl-" + part[2]);
        span.style.flexGrow = String(part[1] / sum);
        bar.appendChild(span);
      });
      box.appendChild(bar);
    }
    var legend = el("div", "tl-legend");
    partsOf.forEach(function (part) { legend.appendChild(add(el("span"), el("i", "tl-" + part[2]), part[0] + " · " + duration(part[1]))); });
    if (rounds !== null) { legend.appendChild(el("span", "tl-f", rounds + (rounds === 1 ? " review round" : " review rounds"))); }
    if (typeof stats.totalSeconds === "number") { legend.appendChild(el("span", "tl-f", "In all: " + duration(stats.totalSeconds))); }
    if (!legend.childNodes.length && !partsOf.length) { return null; }
    box.appendChild(legend);
    return box;
  }

  var HISTORY = table({ "candidate.handed": "Handed over", "check.started": "Check started", "check.finished": "Check finished", "check.retried": "Retried", "check.reused": "Reused", "candidate.stopped": "Stopped", "candidate.merged": "Landed" });
  var historyOpen = Object.create(null);
  function historyFor(doc, item) {
    var rows = journalFor(doc, item.subject, HISTORY);
    if (!rows.length) { return null; }
    var box = el("details", "mh");
    if (historyOpen[item.subject]) { box.open = true; }
    // The steps slide open and closed instead of appearing at once; closing waits for the slide before it closes.
    function slide(open) {
      // Remembered now, not on the toggle event, which fires later: a redraw in between would rebuild it as it was.
      historyOpen[item.subject] = open;
      var from = runHeldHeight(box);
      box.open = open;
      if (open) { runEaseHeight(box, from); return; }
      if (!runMotionWanted(box)) { return; }
      box.open = true;
      var closed = summary.offsetHeight;
      box.style.overflow = "hidden";
      var easing = box.animate([{ height: from + "px" }, { height: closed + "px" }], { id: "run-height", duration: runEaseDuration(from, closed), easing: RUN_EASING });
      easing.onfinish = function () { box.style.overflow = ""; box.open = false; };
      easing.oncancel = function () { box.style.overflow = ""; };
    }
    var last = rows[rows.length - 1];
    var summary = el("summary");
    add(summary, "Merge check", el("span", "mh-sub", " · " + (HISTORY[last.kind] || "") + " " + (spoken(eventTime(last)) || "time unknown") + " · " + rows.length + (rows.length === 1 ? " step" : " steps")));
    var ol = el("ol");
    rows.slice(-8).forEach(function (entry) {
      var li = el("li", entry.kind === "candidate.merged" ? "in" : entry.kind === "candidate.stopped" ? "bad" : "");
      var time = el("time", null, clock(eventTime(entry)) || "Time unknown");
      time.setAttribute("datetime", text(eventTime(entry)));
      add(li, time, el("span", null, text(entry.summary)));
      ol.appendChild(li);
    });
    summary.addEventListener("click", function (event) { event.preventDefault(); slide(!box.open); });
    add(box, summary, ol);
    return box;
  }

  /* 3. Who */
  function drawWho(doc, all) {
    var inner = parts.who;
    var lanes = list(doc.state.lanes).filter(isObject);
    var colours = laneColours(doc, all);
    var focus = isObject(doc.focus) ? doc.focus : {};
    var busy = lanes.filter(function (lane) { return lane.state !== "idle" && lane.state !== "done"; }).length;
    var heading = !lanes.length ? "No one has reported in yet."
      : busy === lanes.length ? lanes.length + (lanes.length === 1 ? " lane is" : " lanes are") + " working on this."
      : busy + " of " + lanes.length + " lanes are working right now.";
    var nodes = [el("div", "kicker", "Who's doing what"), el("h2", "title", heading)];
    var seen = Object.create(null);
    if (lanes.length) {
      nodes.push(el("p", "say", "Each lane is a separate session working in parallel on its own part of the work."));
      var people = el("div", "people");
      lanes.forEach(function (lane) {
        var mark = text(lane.state) + "|" + text(lane.focus) + "|" + text(lane.waitingOn);
        seen[lane.id] = mark;
        var changed = prev.lanes && prev.lanes[lane.id] !== undefined && prev.lanes[lane.id] !== mark && !reduced();
        var row = el("div", "person" + (changed ? " changed" : ""));
        row.style.setProperty("--c", colours[lane.id] || "var(--soft)");
        var who = add(el("div"), el("h3", null, text(lane.title) || text(lane.id)));
        var doing = text(lane.focus) || text(focus[lane.id]);
        if (doing) { who.appendChild(el("p", "doing", doing)); }
        var st = add(el("div", "st"), el("span", "chip" + (LANE_WORDS[lane.state] ? " " + lane.state : ""), LANE_WORDS[lane.state] || "Unknown"));
        if (lane.waitingOn) { st.appendChild(el("span", "on blocked", "waiting on " + text(lane.waitingOn))); }
        else if (heldFor(lane.since)) { st.appendChild(el("span", "on", heldFor(lane.since))); }
        add(row, el("span", "swatch"), who, st);
        people.appendChild(row);
      });
      nodes.push(people);
      prev.lanes = seen;
    }
    var leases = list(doc.state.leases).filter(isObject);
    if (leases.length) {
      var held = el("div", "leases");
      leases.forEach(function (lease) {
        held.appendChild(add(el("span"), el("b", null, text(lease.id)), " held by " + text(lease.holder) + (heldFor(lease.since) ? " " + heldFor(lease.since) : "")));
      });
      nodes.push(el("div", "kicker", "Leases"), held);
    }
    inner.replaceChildren.apply(inner, nodes);
  }

  /* 4. What's left */
  function drawLeft(doc, all) {
    var inner = parts.left;
    var colours = laneColours(doc, all);
    var open = all.filter(function (piece) { return piece.planned || piece.item.stage !== "merged"; });
    var number = el("div", "left-number", String(prev.open === null ? open.length : prev.open));
    number.appendChild(el("small", null, open.length === 1 ? "piece to go" : "pieces to go"));
    countTo(number, prev.open, open.length);
    var wasOpen = prev.dots || {};
    var nowOpen = Object.create(null);
    var nodes = [el("div", "kicker", "What's left"), number,
      el("p", "say", all.length ? (all.length - open.length) + " of " + all.length + " pieces have landed." : "Nothing is planned yet.")];
    if (all.length) {
      var dots = el("div", "dots");
      dots.setAttribute("aria-hidden", "true");
      var closing = 0;
      all.forEach(function (piece) {
        var key = text(piece.item.subject);
        var isOpen = piece.planned || piece.item.stage !== "merged";
        nowOpen[key] = isOpen;
        // A dot that just closed lights up in its lane's colour, then settles to grey.
        var closed = !isOpen && wasOpen[key] === true && !reduced();
        var dot = el("i", isOpen ? "open" : closed ? "closing" : "");
        dot.style.setProperty("--c", colours[piece.item.lane || ""] || "var(--soft)");
        if (closed) { dot.style.animationDelay = closing * 0.06 + "s"; closing += 1; }
        dots.appendChild(dot);
      });
      nodes.push(dots);
      var names = laneNames(doc);
      var byLane = Object.create(null);
      open.forEach(function (piece) { var lane = piece.item.lane || ""; byLane[lane] = (byLane[lane] || 0) + 1; });
      var groups = el("div", "left-groups");
      Object.keys(byLane).forEach(function (lane) {
        var changed = prev.byLane && prev.byLane[lane] !== undefined && prev.byLane[lane] !== byLane[lane] && !reduced();
        groups.appendChild(add(el("div"), el("b", changed ? "changed" : null, String(byLane[lane])), lane ? "with " + (names[lane] || lane) : "not yet assigned"));
      });
      prev.byLane = byLane;
      nodes.push(groups);
    }
    var finish = list(doc.state.finish).filter(isObject);
    if (finish.length) {
      var after = el("div", "after");
      var first = true;
      finish.forEach(function (line) {
        var met = line.met === true;
        var current = !met && first;
        if (!met) { first = false; }
        after.appendChild(add(el("div", met ? "met" : current ? "on" : ""), el("span", null, met ? "Met" : current ? "Next" : "Then"), text(line.text)));
      });
      nodes.push(el("div", "kicker", "The finish line"), after);
    }
    inner.replaceChildren.apply(inner, nodes);
    prev.open = open.length;
    prev.dots = nowOpen;
  }

  /* 5. What happened */
  function drawHappened(doc) {
    var inner = parts.happened;
    var names = laneNames(doc);
    var entries = list(doc.journal).filter(isObject).slice().reverse();
    var legend = el("div", "feed-legend");
    [["merge", "●", "Landed"], ["ready", "◐", "Ready to check"], ["decide", "◆", "Decision"], ["stop", "▲", "Problem"], ["", "·", "Other update"]].forEach(function (entry) {
      legend.appendChild(add(el("span"), el("b", "g " + entry[0], entry[1]), entry[2]));
    });
    var tabs = el("div", "tabs");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "Show");
    TABS.forEach(function (tab) {
      var button = el("button", null, tab[1]);
      button.type = "button";
      button.setAttribute("aria-pressed", String(feedFilter === tab[0]));
      button.setAttribute("data-tab", tab[0]);
      button.addEventListener("click", function () { var focus = focusMark(); feedFilter = tab[0]; feedAll = false; drawHappened(shown); refocus(focus); });
      tabs.appendChild(button);
    });
    var rows = entries.filter(function (entry) { return inTab(entry, feedFilter); });
    var visible = feedAll ? rows : rows.slice(0, 8);
    var ul = el("ul", "feed");
    if (!rows.length) { ul.appendChild(el("li", "waiting", "Nothing here yet.")); }
    visible.forEach(function (entry) {
      var key = text(entry.at) + "|" + String(entry.id);
      var li = el("li", prev.feed && !prev.feed[key] && !reduced() ? "fresh" : "");
      var time = el("time", null, clock(entry.at));
      time.setAttribute("datetime", text(entry.at));
      var glyph = GLYPH[entry.kind] || ["", "·"];
      var mark = el("span", "g " + glyph[0], glyph[1]);
      mark.setAttribute("aria-hidden", "true");
      var who = el("span", "who", [KIND_WORDS[entry.kind] || text(entry.kind), entry.lane ? names[entry.lane] || text(entry.lane) : ""].filter(Boolean).join(" · "));
      if (text(entry.subject)) { add(who, " · ", issue(text(entry.subject))); }
      add(li, time, mark, add(el("p", null, text(entry.summary)), who));
      ul.appendChild(li);
    });
    var seen = Object.create(null);
    entries.forEach(function (entry) { seen[text(entry.at) + "|" + String(entry.id)] = true; });
    prev.feed = seen;
    var nodes = [el("div", "kicker", "What happened"), el("h2", "title", "The story so far, newest first."), legend, tabs, ul];
    if (!feedAll && rows.length > visible.length) {
      var more = el("button", "more", "Show all " + rows.length);
      more.type = "button";
      more.addEventListener("click", function () {
        feedAll = true;
        drawHappened(shown);
        // The button is gone once everything shows; focus moves to the first entry it revealed.
        var first = parts.happened.querySelectorAll(".feed li")[visible.length];
        if (first) { first.setAttribute("tabindex", "-1"); first.setAttribute("data-revealed", ""); first.focus({ preventScroll: true }); }
      });
      nodes.push(more);
    }
    inner.replaceChildren.apply(inner, nodes);
  }

  /* 6. Parked */
  function drawParked(doc) {
    var inner = parts.parked;
    var items = list(doc.state.followups).filter(isObject);
    var nodes = [el("div", "kicker", "Parked for later"),
      el("h2", "title", items.length ? items.length + (items.length === 1 ? " thing found and set aside." : " things found and set aside.") : "Nothing parked.")];
    if (items.length) {
      var ul = el("ul", "parked");
      var known = Object.create(null);
      items.forEach(function (item) {
        var key = text(item.reference) || text(item.id);
        known[key] = true;
        var fresh = prev.parked && !prev.parked[key] && !reduced();
        ul.appendChild(add(el("li", fresh ? "fresh" : ""), add(el("span", "p"), issue(key)), text(item.title)));
      });
      nodes.push(ul);
      prev.parked = known;
    }
    inner.replaceChildren.apply(inner, nodes);
  }

  function usable(doc) { return isObject(doc) && isObject(doc.state); }

  function draw(doc) {
    if (!usable(doc)) {
      parts.now.replaceChildren(el("div", "kicker", pageTitle()), el("h1", "title", "The delivery record could not be read."));
      ["line", "who", "left", "happened", "parked"].forEach(function (name) { parts[name].replaceChildren(); });
      return;
    }
    var focus = focusMark();
    // A redraw can remove the id under the pointer without a mouseleave, so its card goes too.
    hideCard();
    shown = doc;
    var all = pieces(doc);
    drawNow(doc, all);
    drawLine(doc, all);
    drawWho(doc, all);
    drawLeft(doc, all);
    drawHappened(doc);
    drawParked(doc);
    refocus(focus);
  }
  // A redraw replaces the stops, links and tabs; keyboard focus stays on the one it was on.
  function focusMark() {
    var active = document.activeElement;
    if (!active || !app.contains(active) || !active.getAttribute) { return null; }
    var names = ["data-subject", "data-ticket", "data-tab"];
    for (var i = 0; i < names.length; i += 1) {
      if (active.hasAttribute(names[i])) {
        // The same id can sit under its stop and in the detail; focus comes back to the same place.
        return { node: active, name: names[i], value: active.getAttribute(names[i]), within: active.closest("#detail, .stop-label, .feed, .parked, .checking, .kicker, .chapter") };
      }
    }
    return null;
  }
  function refocus(mark) {
    if (!mark || app.contains(mark.node)) { return; }
    // Matched by attribute value, never by a selector built from record text.
    var same = Array.prototype.filter.call(app.querySelectorAll("[" + mark.name + "]"), function (node) { return node.getAttribute(mark.name) === mark.value; });
    var place = mark.within ? (mark.within.id ? "#" + mark.within.id : "." + mark.within.className.split(" ")[0]) : null;
    var again = same.filter(function (node) { return !place || node.closest(place); })[0] || same[0];
    if (again) { again.focus({ preventScroll: true }); }
  }

  // A record that fails to draw leaves the last good drawing in place and says so.
  function safely(doc) {
    try { draw(doc); lastGood = doc; } catch (error) {
      if (lastGood && doc !== lastGood) {
        if (connection === "live") { connection = "failed"; }
        try { draw(lastGood); } catch (again) { /* the last drawing stays */ }
      }
    }
  }

  safely(embedded);
  var store = globalThis.deliveryStateStore;
  if (isObject(store)) {
    if (typeof store.onStatus === "function") {
      try {
        // The adapter reports the connection with every pushed record; only a change is drawn.
        store.onStatus(function (live) {
          var next = live ? "live" : "down";
          if (next === connection) { return; }
          connection = next;
          safely(lastGood || shown);
        });
      } catch (error) { /* a store without a status line still serves its pushes and read below */ }
    }
    var pushed = false;
    if (typeof store.subscribe === "function") {
      try {
        store.subscribe(function (doc) {
          if (!usable(doc)) { return; }
          pushed = true;
          connection = "live";
          app.setAttribute("data-source", "live");
          safely(doc);
          refreshRuns();
        });
      } catch (error) { /* a store that cannot subscribe still serves its read below */ }
    }
    if (typeof store.read === "function") {
      Promise.resolve().then(function () { return store.read(); }).then(function (doc) {
        if (pushed || !usable(doc)) { return; }
        app.setAttribute("data-source", "live");
        if (connection === "saved") { connection = "connected"; }
        safely(doc);
      }, function () {
        if (!pushed) { connection = "unreadable"; safely(lastGood || shown); }
      });
    }
  }
  // Relative times and running checks follow the page's own clock between writes.
  if (typeof setInterval === "function") { setInterval(function () { safely(lastGood || shown); }, 30000); }
  // The run line is drawn at the page's width, so a new width redraws it.
  var resizing = null;
  if (typeof addEventListener === "function") {
    var drawnWidth = innerWidth;
    // Only a new width redraws: a phone's address bar showing or hiding changes the height alone.
    addEventListener("resize", function () {
      if (innerWidth === drawnWidth) { return; }
      drawnWidth = innerWidth;
      clearTimeout(resizing);
      resizing = setTimeout(function () { safely(lastGood || shown); }, 200);
    });
  }
})();
