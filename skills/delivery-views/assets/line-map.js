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
  // What the reader picked to show of the story: a tone to filter by, and a depth this visit, until it is remembered.
  var storyFilter = "all";
  var storyDepthChosen = null;
  // What the page last showed, so a change can be animated rather than just swapped in.
  var prev = { figures: Object.create(null), runningCheck: null, stages: null, story: null, progress: null, lanes: null, byLane: null, parked: null, dots: null, selected: null, lineScroll: null,
    checkingHeight: null };
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
  /**
   * Words holding numbers, put into `node`. A number that changed since this place on the page was last drawn flips
   * its changed digits over from the old ones, lined up from the right as on a counter, and a digit with none before
   * it flips in from blank. The words stay one plain text, the new value, so the line never moves: while a digit turns,
   * a highlight hides it and a layer drawn over its place, with no words of its own, rolls the old digit out and the
   * new one in. A first draw, an unchanged number, a reader who asked for less motion and a browser without
   * highlights get the words as they are.
   */
  function figures(node, key, words) {
    var was = prev.figures[key];
    prev.figures[key] = words;
    node.textContent = words;
    var registry = highlights();
    if (typeof was !== "string" || was === words || reduced() || !registry) { return node; }
    var old = was.match(/\d+/g) || [];
    var turns = [];
    var at = 0, offset = 0;
    words.split(/(\d+)/).forEach(function (part, i) {
      if (i % 2 === 1 && old[at] !== undefined) {
        var from = old[at];
        for (var d = 0; d < part.length; d += 1) {
          var k = from.length - part.length + d;
          var before = k >= 0 ? from.charAt(k) : "";
          if (before !== part.charAt(d)) { turns.push({ at: offset + d, from: before, to: part.charAt(d) }); }
        }
      }
      at += i % 2;
      offset += part.length;
    });
    // Measured once the draw has put the words on the page.
    if (turns.length) { Promise.resolve().then(function () { flip(node, turns, registry); }); }
    return node;
  }
  function highlights() {
    return typeof CSS === "object" && CSS && CSS.highlights && typeof Highlight === "function" && typeof StaticRange === "function" ? CSS.highlights : null;
  }
  /** A redraw mid-turn takes the turning words off the page; the digits it hid there are no longer held. */
  function dropTurned() {
    var hold = highlights() ? highlights().get("flip-hold") : null;
    if (hold) { hold.forEach(function (range) { if (!range.startContainer.isConnected) { hold.delete(range); } }); }
  }
  function flip(node, turns, registry) {
    var words = node.firstChild;
    if (!node.isConnected || !words || words.nodeType !== 3) { return; }
    var hold = registry.get("flip-hold");
    if (!hold) { hold = new Highlight(); registry.set("flip-hold", hold); }
    node.classList.add("flipping");
    var box = node.getBoundingClientRect();
    var digits = turns.map(function (turn) {
      // Held as a static range: a live one taken off the page would fold into the page around it and look still there.
      var range = new StaticRange({ startContainer: words, startOffset: turn.at, endContainer: words, endOffset: turn.at + 1 });
      hold.add(range);
      var digit = el("span", "flip");
      digit.setAttribute("aria-hidden", "true");
      digit.setAttribute("data-from", turn.from);
      digit.setAttribute("data-to", turn.to);
      var measure = document.createRange();
      measure.setStart(words, turn.at);
      measure.setEnd(words, turn.at + 1);
      if (typeof measure.getBoundingClientRect === "function") {
        var place = measure.getBoundingClientRect();
        digit.style.left = place.left - box.left + "px";
        digit.style.top = place.top - box.top + "px";
        digit.style.width = place.width + "px";
        digit.style.height = place.height + "px";
      }
      node.appendChild(digit);
      return { range: range, digit: digit };
    });
    // The digits land together; then the words show their own digits again, in the same frame, and the layer goes.
    digits[0].digit.addEventListener("animationend", function (event) {
      if (event.animationName !== "flip-in") { return; }
      digits.forEach(function (one) { hold.delete(one.range); node.removeChild(one.digit); });
      node.classList.remove("flipping");
    });
  }
  /**
   * The same for a count drawn as SVG text, set in the page's monospace so its characters line up from the anchored
   * right: the old characters that changed fall away under the new ones, each layer showing only within the count's line.
   * The drawing's label says the final count, so the layers are drawing only.
   */
  function figureText(svg, key, attributes, words) {
    var was = prev.figures[key];
    prev.figures[key] = words;
    var node = shape(svg, "text", attributes, words);
    if (typeof was !== "string" || was === words || reduced()) { return node; }
    var width = Math.max(was.length, words.length);
    var changed = function (i) { return was.charAt(i - width + was.length) !== words.charAt(i - width + words.length); };
    // A layer spells its words a character at a time, showing only the characters it is for.
    var spell = function (target, chars, shown, hidden) {
      chars.split("").forEach(function (char, j) { shape(target, "tspan", shown(width - chars.length + j) ? {} : { class: hidden }, char); });
      return target;
    };
    node.textContent = "";
    spell(node, words, function (i) { return !changed(i); }, "flip-gap");
    spell(shape(svg, "text", Object.assign({}, attributes, { class: attributes.class + " flip-out" })), was, changed, "flip-hide");
    spell(shape(svg, "text", Object.assign({}, attributes, { class: attributes.class + " flip-in" })), words, changed, "flip-hide");
    return node;
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
  // The delivery's plan, as the renderer validated and embedded it: { plan, clipped? }, { unreadable } naming the key
  // path that failed, or { absent } naming the plan the record says is on file when no plan.json came with it. With no
  // block the page has no plan chapter at all.
  var PLAN = null;
  var planBlock = document.getElementById("delivery-plan");
  if (planBlock) {
    try { PLAN = JSON.parse(planBlock.textContent); } catch (error) { PLAN = null; }
    if (!isObject(PLAN) || (!isObject(PLAN.plan) && typeof PLAN.unreadable !== "string" && typeof PLAN.absent !== "string")) { PLAN = { unreadable: "" }; }
  }
  var plan = PLAN && isObject(PLAN.plan) ? PLAN.plan : null;
  // The supervisor's "Needs you" list, as the renderer embedded it from the record: { running, at, staleAfter, items },
  // or { unreadable }. With no block no supervisor wrote one, and the Now chapter has no Needs you box.
  function readNeeds(textContent) {
    try { var block = JSON.parse(textContent); } catch (error) { return { unreadable: true }; }
    return isObject(block) ? block : { unreadable: true };
  }
  var needsBlock = document.getElementById("delivery-needs");
  var NEEDS = needsBlock ? readNeeds(needsBlock.textContent) : null;
  // What a closed delivery left open, as the renderer derived it from the record and said it from the table.
  var leftBlock = document.getElementById("delivery-left-open");
  var LEFT_OPEN = leftBlock ? readNeeds(leftBlock.textContent) : null;
  function changeLink(changeRequest) {
    var target = changeTarget(changeRequest);
    if (!target) { return el("span", "pr-num", text(changeRequest)); }
    var link = el("a", "issue-link pr-num", target.label);
    link.setAttribute("href", target.href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    var identity = (typeof target.repository === "string" && target.repository ? target.repository + " " : "") + target.label;
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
  // A length of time as a person says it: "less than a minute", "40 minutes", "9 hours", "an hour 20 minutes", "3 days".
  function spokenDuration(total) {
    if (typeof total !== "number" || !isFinite(total) || total < 0) { return ""; }
    var minutes = Math.round(total / 60);
    if (minutes < 1) { return "less than a minute"; }
    if (minutes < 60) { return minutes === 1 ? "a minute" : minutes + " minutes"; }
    var hours = Math.floor(minutes / 60), rest = minutes % 60;
    if (hours < 48) {
      return (hours === 1 ? "an hour" : hours + " hours") + (rest ? " " + rest + (rest === 1 ? " minute" : " minutes") : "");
    }
    var days = Math.round(hours / 24);
    return days + " days";
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
    var parts = clockParts(at);
    return !parts ? "" : parts.day ? parts.day + ", " + parts.time : parts.time;
  }
  /** A time as its clock time and, for another day than today, its day: { time, day }, or null when unreadable. */
  function clockParts(at) {
    var t = typeof at === "string" ? Date.parse(at) : NaN;
    if (isNaN(t)) { return null; }
    var date = new Date(t);
    var time = ((date.getHours() + 11) % 12 + 1) + ":" + (date.getMinutes() < 10 ? "0" : "") + date.getMinutes() + (date.getHours() < 12 ? " am" : " pm");
    // An entry from another day says which day, so a run that spans days reads in order.
    return { time: time, day: new Date(Date.now()).toDateString() === date.toDateString() ? "" : MONTHS[date.getMonth()] + " " + date.getDate() };
  }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var IN_CHECKS = table({ syncing: true, synced: true, checking: true, checked: true, merging: true });
  var STAGE_WORDS = table({
    merged: "Landed", stopped: "Stopped, waiting for its lane", queued: "Ready, waiting to be checked",
    pending: "Handed over, waiting for the lead to accept",
    syncing: "Being checked before merge", synced: "Being checked before merge", checking: "Being checked before merge",
    checked: "Checked, about to merge", merging: "Merging", planned: "Not started", building: "Being built", review: "In review",
    settled: "Closed, no change needed", nochange: "Handed over with no change needed, waiting for the lead"
  });
  var LANE_WORDS = table({ idle: "Standing by", assigned: "Assigned", working: "Building", reviewing: "In review", handed: "Handed over", blocked: "Blocked", waiting: "Waiting", done: "Done" });
  // The states of a check that ran in this run, and so carries a start time.
  var RAN = table({ running: true, passed: true, failed: true, timed_out: true });
  var STEP_WORDS = table({ pending: "waiting", running: "running", passed: "passed", failed: "failed", skipped: "skipped", timed_out: "timed out", reused: "reused" });
  var DONE = table({ passed: true, failed: true, skipped: true, timed_out: true, reused: true });
  // One drawn set, each mark centred in the same box, so none depends on how a font sizes a symbol.
  var MARKS = table({
    merge: '<circle cx="7" cy="7" r="4.5" fill="currentColor"/>',
    ready: '<circle cx="7" cy="7" r="3.9" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M7 2.5a4.5 4.5 0 0 0 0 9z" fill="currentColor"/>',
    decide: '<path d="M7 2.4 11.6 7 7 11.6 2.4 7z" fill="currentColor" stroke="currentColor" stroke-linejoin="round"/>',
    stop: '<path d="M7 3 11.5 10.8H2.5z" fill="currentColor" stroke="currentColor" stroke-linejoin="round"/>',
    wait: '<path d="M7 2.2 11.8 7 7 11.8 2.2 7z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    note: '<path d="M3 7h8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
  });
  // The agents' icons, from the Line Map persona set: marks on lines, on a 24px grid in round 1.75 strokes. Each part is a
  // path (d) or a circle (c: cx, cy, r), built as SVG elements; a core part, the agent itself, is filled.
  var PERSONAS = table({
    lead: [{ c: [4.6, 12, 2.6], core: true }, { d: "M7.2 12H16.6" }, { d: "M9.5 12V9.5Q9.5 6 13 6H16.6" }, { d: "M9.5 12V14.5Q9.5 18 13 18H16.6" }, { c: [18.9, 6, 1.9] }, { c: [18.9, 12, 1.9] }, { c: [18.9, 18, 1.9] }],
    lane: [{ d: "M2.5 12H4.6" }, { c: [7, 12, 2.4] }, { d: "M9.4 12H12.1" }, { c: [15.5, 12, 3.4], core: true }, { d: "M18.9 12H21.5" }],
    runner: [{ c: [4.1, 6, 1.9] }, { c: [4.1, 18, 1.9] }, { d: "M6.4 6H8.5Q12 6 12 9.5V12" }, { d: "M6.4 18H8.5Q12 18 12 14.5V12" }, { d: "M12 12H13.4" }, { c: [16, 12, 2.6], core: true }, { d: "M18.6 12H21.5" }],
    supervisor: [{ d: "M4 10.5C7 5.8 17 5.8 20 10.5C17 15.2 7 15.2 4 10.5Z" }, { c: [12, 10.5, 2.4], core: true }, { d: "M3 19.5H21" }, { c: [7.5, 19.5, 1.2], core: true }, { c: [16.5, 19.5, 1.2], core: true }]
  });
  // Marks drawn on the persona grid that are not personas: a landed piece takes the familiar git merge, a branch
  // curving into the trunk.
  var GRID_MARKS = {
    merge: [{ c: [6.5, 5.5, 2.25] }, { c: [6.5, 18.5, 2.25] }, { c: [17.5, 12, 2.25] }, { d: "M6.5 7.75V16.25" }, { d: "M6.5 7.75Q6.5 12 15.25 12" }]
  };
  function persona(name, className) {
    var svgNs = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "persona " + (className || "is-" + name));
    svg.setAttribute("viewBox", "0 0 24 24");
    // Hidden from assistive technology: the words beside it always say the same.
    svg.setAttribute("aria-hidden", "true");
    runDrawParts(svg, PERSONAS[name] || GRID_MARKS[name]);
    return svg;
  }
  function glyph(tone) {
    var node = el("span", "g " + (tone === "note" ? "" : tone));
    node.innerHTML = '<svg viewBox="0 0 14 14" aria-hidden="true">' + MARKS[tone] + "</svg>";
    return node;
  }
  // The chapters are built once; each draw fills them.
  var CHAPTERS = [["now", "Now"], ["line", "The line"], ["who", "Who"], ["left", "What's left"], ["happened", "What happened"], ["parked", "Parked"]];
  // The plan sits after the work ahead and before the history, and only when the renderer embedded one.
  if (PLAN) { CHAPTERS.splice(4, 0, ["plan", "The plan"]); }
  var parts = Object.create(null);
  // The lead's check choices the reader has opened, by subject.
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
    placeCard(target);
  }
  /** Shows the card beside what is pointed at: below it, or above when there is no room below. */
  var cardTarget = null;
  function placeCard(target) {
    cardTarget = target;
    card.hidden = false;
    if (typeof target.getBoundingClientRect !== "function" || typeof innerWidth !== "number") { return; }
    var box = target.getBoundingClientRect();
    var width = card.offsetWidth || Math.min(300, innerWidth - 32);
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

  // What a thing waits on, and who waits on it: the state tool's sentences, printed as they are and never worded here.
  // The story a wait names is linked where the sentence names it.
  function waitsOf(doc, name, subject) {
    return list(doc[name]).filter(function (wait) { return isObject(wait) && wait.subject === subject && typeof wait.words === "string"; });
  }
  function waitWords(doc, subject) {
    return waitsOf(doc, "waits", subject).concat(waitsOf(doc, "waitedOn", subject)).map(function (wait) { return wait.words; });
  }
  function waitLine(tag, cls, wait) {
    var node = el(tag, cls);
    var named = typeof wait.on === "string" && wait.on !== "lead" ? wait.on : "";
    var at = named ? wait.words.indexOf(named) : -1;
    if (at < 0) { return add(node, wait.words); }
    return add(node, wait.words.slice(0, at), issue(named), wait.words.slice(at + named.length));
  }
  function waitLines(doc, subject, tag, cls) {
    return waitsOf(doc, "waits", subject).concat(waitsOf(doc, "waitedOn", subject)).map(function (wait) { return waitLine(tag, cls, wait); });
  }

  /** A piece that is done: merged through the queue, or planned work the lead settled with no change needed. */
  function isLanded(piece) { return piece.planned ? piece.item.stage === "settled" : piece.item.stage === "merged"; }
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
  // A lane's colour comes from its id, never from where the record lists it, so no row moving changes a lane's colour.
  function laneColour(lane) {
    if (!lane) { return "var(--soft)"; }
    var numbered = /^lane-(\d+)$/.exec(lane);
    var slot = 0;
    if (numbered) { slot = (Number(numbered[1]) + 5) % 6; }
    else { for (var i = 0; i < lane.length; i += 1) { slot = (slot * 31 + lane.charCodeAt(i)) % 6; } }
    return "var(--l" + (slot + 1) + ")";
  }
  function laneColours(doc, all) {
    var colours = Object.create(null);
    list(doc.state.lanes).forEach(function (lane) { if (isObject(lane) && lane.id) { colours[lane.id] = laneColour(text(lane.id)); } });
    all.forEach(function (piece) { var lane = piece.item.lane || ""; colours[lane] = laneColour(lane); });
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
    if (piece.planned) {
      if (item.stage === "settled") { return "merged"; }  // done, as a landed piece is, though it never entered the queue
      return item.stage === "planned" ? "later" : item.stage === "building" || item.stage === "review" ? "active" : "unknown";
    }
    if (item.stage === "merged") { return "merged"; }
    if (item.stage === "stopped") { return "held"; }
    if (IN_CHECKS[item.stage] || (nowSubject !== null && subjectOf(item) === nowSubject)) { return "now"; }
    if (item.stage !== "queued") { return "unknown"; }
    return item.pending === true ? "ready pending" : "ready";
  }
  // A check's readable name: the title its provider or binding gave, cut short past a line's worth, or else its id. The
  // id stays the check's name for everything that matches on it, such as a lane's notes and the rows being redrawn.
  var TITLE_SHOWN = 80;
  function checkTitle(title) { var words = text(title); return words.length > TITLE_SHOWN ? words.slice(0, TITLE_SHOWN - 1) + "…" : words; }
  function stepName(step) { return checkTitle(step.title) || text(step.label); }
  function failures(item) {
    return list(item.checks).filter(function (check) { return isObject(check) && (check.state === "failed" || check.state === "timed_out"); })
      .map(function (check) {
        var tests = list(check.failures).map(text).filter(Boolean);
        return (checkTitle(check.title) || text(check.id)) + " " + (check.state === "timed_out" ? "timed out" : "failed") + (tests.length ? ": " + tests.join(", ") : "");
      });
  }
  function stageKey(item) {
    if (item.pending === true && item.stage === "queued") { return "pending"; }
    return isObject(item.noChange) && item.stage === "review" ? "nochange" : text(item.stage);
  }
  function stageWords(item) { return STAGE_WORDS[stageKey(item)] || "Stage not recognised"; }
  /**
   * The base re-run a piece waits on: its check failed and the queue is running the failing checks on the main line to
   * tell whether the failure is the main line's own. Only the piece the checking panel names, while it is being checked.
   */
  function rerunOf(doc, item) {
    var current = checkingNow(doc);
    if (!current || !isObject(current.baseRerun) || item.stage !== "checking") { return null; }
    return current.subject !== undefined && current.subject !== null && String(current.subject) === subjectOf(item) ? current.baseRerun : null;
  }
  var RERUN_WORDS = "Failed, waiting on the main line's rerun";
  function pieceWords(doc, item) { return rerunOf(doc, item) ? RERUN_WORDS : stageWords(item); }
  /** A piece that hadn't landed when its delivery closed isn't in any stage any more. */
  function unlandedAtClose(doc, piece) { return Boolean(closedEntry(doc)) && !isLanded(piece); }
  // Each stage's mark, in the page's one grammar: circles for how far a piece has got, a hollow diamond for a hand-over
  // waiting on the lead, a triangle for a stop. The stylesheet draws the shape; a stage the page does not know gets none.
  var STAGE_MARKS = table({ planned: true, building: true, review: true, pending: true, queued: true, syncing: true, synced: true,
    checking: true, checked: true, merging: true, merged: true, stopped: true, settled: true, nochange: true });
  function stageIcon(item) {
    var key = stageKey(item);
    if (!STAGE_MARKS[key]) { return null; }
    // A landed piece has merged into the main line, so it takes the merge's own drawing: two lanes turning into one.
    if (key === "merged") { return persona("merge", "d-stage-icon is-merged"); }
    // Hidden from assistive technology, since the words beside it say the same.
    var mark = el("span", "d-stage-icon is-" + key);
    mark.setAttribute("aria-hidden", "true");
    return mark;
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
   * each naming the batch's members; that is the batch while every piece in checks belongs to
   * it. Members that already landed from it stay listed; a member sent back to the queue drops out, and so
   * does one checked again on its own since.
   */
  function batchNow(doc, all) {
    // Only check starts decide: the latest must be a batch start, which names its members. Other entries after it,
    // such as a lane's note, change nothing.
    var starts = list(doc.journal).filter(function (entry) { return isObject(entry) && entry.kind === "check.started"; });
    var last = starts[starts.length - 1];
    if (!last) { return null; }
    var members = [];
    // A start names its batch's members, in order; one with no batch is a piece checked alone.
    list(isObject(last.data) ? last.data.batch : null).forEach(function (subject) { if (typeof subject === "string" && members.indexOf(subject) < 0) { members.push(subject); } });
    if (members.length < 2) { return null; }
    var queued = all.filter(function (piece) { return !piece.planned; }).map(function (piece) { return piece.item; });
    var inChecks = queued.filter(function (item) { return IN_CHECKS[item.stage]; });
    if (!inChecks.length || !inChecks.every(function (item) { return members.indexOf(subjectOf(item)) >= 0; })) { return null; }
    var batch = members.map(function (subject) {
      return queued.filter(function (item) { return subjectOf(item) === subject && (IN_CHECKS[item.stage] || item.stage === "merged"); })[0];
    }).filter(Boolean);
    return batch.length > 1 ? batch : null;
  }
  /** The entry that closed this delivery, or null while it is open: work resuming after a close reopens it. */
  function closedEntry(doc) {
    var closed = null;
    list(doc.journal).filter(isObject).forEach(function (entry) {
      if (entry.kind === "delivery.closed") { closed = entry; }
      else if (entry.kind === "lane.assigned" || entry.kind === "candidate.handed" || entry.kind === "check.started") { closed = null; }
    });
    return closed;
  }
  /** A piece with no lane: still to be assigned while the delivery is open, and simply without one once it closed. */
  function noLane(doc) { return closedEntry(doc) ? "No lane" : "Not yet assigned"; }
  /** A lane by its id, read as a name: lane-1 is Lane 1, never a count of lanes; lane-backend is the Backend lane. */
  function laneLabel(lane) {
    if (lane === "lead") { return "Lead"; }
    var numbered = /^lane-(\d+)$/.exec(lane);
    return numbered ? "Lane " + numbered[1]
      : lane.replace(/^lane-/, "").replace(/^./, function (first) { return first.toUpperCase(); }) + " lane";
  }
  /** Whether the plan took this piece out: it was no longer meant to land. */
  function removedFromPlan(piece) {
    return Boolean(plan) && planUnits(plan.units).some(function (unit) { return unit.removed === true && unit.subject === piece.item.subject; });
  }
  /** Landed pieces out of everything meant to land: a closed delivery counts plan units that never reached the line, and not ones the plan removed. */
  function closedTally(all) {
    var meant = all.filter(function (piece) { return !removedFromPlan(piece); });
    var landed = meant.filter(isLanded).length;
    var missing = plan ? liveUnits().filter(function (unit) { return !all.some(function (piece) { return piece.item.subject === unit.subject; }); }).length : 0;
    return { landed: landed, total: meant.length + missing, placed: meant.length };
  }
  // A review round's own record: its facts.
  function roundOf(entry) {
    return isObject(entry.data) && entry.kind === "review.round" ? entry.data : null;
  }
  // The entry that says a review was kept, and what it names, from its facts.
  var KEPT = table({ "review.recorded": true });
  function keptReview(entry) {
    return entry.kind === "review.recorded" && isObject(entry.data) ? { digest: entry.data.digest, candidateRef: entry.data.candidateRef } : null;
  }
  function journalFor(doc, subject, kinds) {
    return list(doc.journal).filter(function (entry) { return isObject(entry) && entry.subject === subject && (!kinds || kinds[entry.kind]); });
  }

  /* 1. Now */
  function drawNow(doc, all) {
    // A redraw can remove what the card describes without a mouseleave; such a card hides.
    if (!card.hidden && cardTarget && !cardTarget.isConnected) { hideCard(); }
    var inner = parts.now;
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var landed = all.filter(isLanded).length;
    var title = el("h1", "title");
    var closed = closedEntry(doc);
    var tally = closed ? closedTally(all) : null;
    if (closed && !tally.total) { add(title, all.length ? "Closed with nothing left to land." : "Closed with nothing on the line."); }
    else if (closed && tally.landed === tally.total) { add(title, "Delivered."); }
    else if (closed) { add(title, "Closed with ", figures(el("b"), "now", tally.landed + " of " + tally.total), " pieces landed."); }
    else if (!all.length) { add(title, "Nothing on the line yet."); }
    else if (landed === all.length) { add(title, "Every piece has landed."); }
    else { add(title, figures(el("b"), "now", landed + " of " + all.length), " pieces have landed."); }
    var outcome = plan && isObject(plan.outcome) ? text(plan.outcome.summary) : "";
    var headline = text(meta.headline) || pageTitle();
    // The outcome is said once, under the headline: a record headline that only repeats it leaves the kicker to the ticket.
    var kicker = el("div", "kicker");
    if (headline !== outcome) { add(kicker, el("span", "headline", headline)); }
    // The tracker item the delivery's work is listed under, linked when the renderer linked it.
    if (text(meta.trackerParent)) { add(kicker, kicker.firstChild ? " · " : null, issue(text(meta.trackerParent))); }
    else if (!kicker.firstChild) { add(kicker, el("span", "headline", pageTitle())); }
    // How long the delivery has run, from the record's first entry: to now while it delivers, to its close once delivered.
    // The start, and for a closed one the end, read on pointing at it, in local time with UTC beside it.
    var first = list(doc.journal).filter(isObject)[0];
    var begun = first && first.id === 1 ? seconds(first.at) : null;
    var ended = closed ? seconds(eventTime(closed)) : Date.now() / 1000;
    if (begun !== null && ended !== null && ended >= begun) {
      var minutes = Math.round((ended - begun) / 60), hours = Math.floor(minutes / 60);
      var lasted = minutes < 60 ? minutes + " min" : hours < 48 ? hours + " h" + (minutes % 60 ? " " + minutes % 60 + " min" : "") : Math.round(hours / 24) + " days";
      var stamp = function (at) { var date = new Date(at * 1000); return date.toLocaleString() + " (" + date.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") + ")"; };
      var time = el("time", "kicker-time", closed ? "delivered in " + lasted : "delivering · " + lasted);
      time.setAttribute("datetime", text(first.at));
      time.setAttribute("tabindex", "0");
      // The page's own card says the ends, as a ticket's does, since nothing on the page carries a native tooltip.
      var ends = closed ? [["Started", begun], ["Closed", ended]] : [["Started", begun]];
      var showEnds = function () {
        card.replaceChildren.apply(card, ends.map(function (end) { return el("div", "ic-s", end[0] + " " + stamp(end[1])); }));
        placeCard(time);
      };
      time.addEventListener("mouseenter", showEnds);
      time.addEventListener("focus", showEnds);
      time.addEventListener("mouseleave", hideCard);
      time.addEventListener("blur", hideCard);
      add(kicker, " · ", time);
    }
    var nodes = [kicker, title];
    // The outcome, then how much of the plan is proven and how often it changed, linked to the plan chapter.
    var planLink = planNow(doc);
    if (outcome || planLink) { nodes.push(add(el("p", "plan-now"), outcome || null, planLink)); }
    var status = sayLine(doc, all);
    if (status) { nodes.push(el("p", "say", status)); }
    // What the lead waits on in another delivery sharing the base, and what waits on the lead, as the state tool says.
    waitLines(doc, "lead", "p", "say say-wait").forEach(function (line) { nodes.push(line); });
    var panel = closed ? null : checkingPanel(doc, all);
    if (panel) { nodes.push(panel); }
    var supervisor = needsYou(doc, all, Boolean(closed));
    if (supervisor) { nodes.push(supervisor); }
    var left = closed ? leftOpen(doc, all) : null;
    if (left) { nodes.push(left); }
    var partway = closeStoppedPartway(doc, closed);
    if (partway) { nodes.push(partway); }
    nodes.push(journey(doc, all, closed ? tally.landed : landed));
    // Under the line, three facts in one shape: how many stories have landed, what landed last, and the record's state.
    nodes.push(add(el("div", "facts"), storiesFact(all, closed ? tally : null), lastLanded(doc, all), since(doc, meta, closed)));
    var was = prev.checkingHeight;
    inner.replaceChildren.apply(inner, nodes);
    // A live update that changes the sheet's height, a check starting or a failure listed, eases it there instead of jumping.
    prev.checkingHeight = panel ? panel.offsetHeight : null;
    if (panel && was && was !== prev.checkingHeight) { easeSheet(panel, was); }
  }

  /**
   * The sentence under the headline. A lead's override is shown whole; the derived status repeats what the
   * panels below already say, so the page says what landed last instead.
   */
  function sayLine(doc, all) {
    var status = isObject(doc.status) ? doc.status : {};
    if (status.source === "override" && text(status.text)) { return text(status.text); }
    var merges = list(doc.journal).filter(function (entry) { return isObject(entry) && entry.kind === "candidate.merged"; });
    if (latestMerge(doc)) { return ""; }
    if (merges.length) { return "Work has landed; merge time unknown."; }
    return all.length ? "Nothing has landed yet." : "";
  }
  function latestMerge(doc) {
    return list(doc.journal).filter(function (entry) { return isObject(entry) && entry.kind === "candidate.merged" && seconds(eventTime(entry)) !== null; })
      .sort(function (a, b) { return seconds(eventTime(a)) - seconds(eventTime(b)) || a.id - b.id; }).pop();
  }
  /** One fact under the line: a quiet label, what it says, and a quieter line of detail. */
  function fact(cls, label, value, detail) {
    return add(el("div", "fact" + (cls ? " " + cls : "")), add(el("div", "fact-label"), label), add(el("div", "fact-value"), value),
      detail ? add(el("div", "fact-detail"), detail) : null);
  }
  /**
   * How far the delivery has come, from the record: the stories landed out of its total in the display face, a track in
   * tones of the landed colour (landed, in the queue, being built), and a line counting the queue and the build. A
   * closed delivery shows its final count.
   */
  function storiesFact(all, tally) {
    var landed = tally ? tally.landed : all.filter(isLanded).length;
    var total = tally ? tally.total : all.length;
    // Only work that is moving is counted: a stopped piece is held, not queued, and a planned story not yet started is
    // not being built.
    var queued = tally ? 0 : all.filter(function (piece) { return !piece.planned && !isLanded(piece) && piece.item.stage !== "stopped"; }).length;
    var building = tally ? 0 : all.filter(function (piece) { return piece.planned && (piece.item.stage === "building" || piece.item.stage === "review"); }).length;
    var value = add(el("span", "fact-count"), String(landed), el("span", "fact-of", " of " + total + " landed"));
    var track = el("span", "fact-track");
    track.setAttribute("aria-hidden", "true");
    [["landed", landed], ["queued", queued], ["building", building]].forEach(function (part) {
      if (!part[1] || !total) { return; }
      var span = el("i", "is-" + part[0]);
      span.style.width = (part[1] / total * 100) + "%";
      track.appendChild(span);
    });
    var said = [queued ? queued + " in the queue" : "", building ? building + " being built" : ""].filter(Boolean).join(" · ");
    return fact("stories", "Stories", value, add(el("span", "fact-progress"), track, said ? el("span", "fact-said", said) : null));
  }
  /** A pull request as a fact's quiet reference: its number, linked to the forge when the renderer gave its address. */
  function pullRef(changeRequest) {
    var target = changeTarget(changeRequest);
    if (!target) { return el("span", "fact-ref", text(changeRequest)); }
    var link = el("a", "fact-ref", target.label);
    link.setAttribute("href", target.href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    link.setAttribute("aria-label", "Open pull request " + (typeof target.repository === "string" && target.repository ? target.repository + " " : "") + target.label);
    return link;
  }
  /** The piece that landed last, by its title, with its ticket, its pull request when the forge snapshot knows it, and when. */
  function lastLanded(doc, all) {
    var landed = latestMerge(doc);
    if (!landed) { return null; }
    var item = all.filter(function (piece) { return piece.item.subject === landed.subject; })[0];
    var when = spoken(eventTime(landed));
    var request = item && changeTarget(item.item.changeRequest) ? pullRef(item.item.changeRequest) : null;
    var ticket = text(landed.subject) ? issue(text(landed.subject)) : null;
    if (ticket) { ticket.classList.add("fact-ref"); }
    var parts = [ticket, request, when].filter(Boolean);
    var detail = parts.length ? add.apply(null, [el("span")].concat(parts.reduce(function (made, part, i) { return made.concat(i ? [" · ", part] : [part]); }, []))) : null;
    return fact("", "Last landed", item ? titleOf(doc, item.item) : text(landed.subject), detail);
  }

  /** A check's row in the checking sheet: its mark (a ring whose arc turns while it runs) and what it is doing. */
  function runRow(step) {
    var row = el("div", "ck-run");
    var mark = document.createElementNS(SVG, "svg");
    mark.setAttribute("class", "ck-run-mark");
    mark.setAttribute("viewBox", "0 0 16 16");
    mark.setAttribute("aria-hidden", "true");
    shape(mark, "circle", { class: "rm-track", cx: 8, cy: 8, r: 6.25 });
    shape(mark, "circle", { class: "rm-arc", cx: 8, cy: 8, r: 6.25, pathLength: 100 });
    shape(mark, "path", { class: "rm-check", d: "M5.1 8.4 7.1 10.4 11 6.2", pathLength: 100 });
    shape(mark, "path", { class: "rm-cross", d: "M5.9 5.9 10.1 10.1M10.1 5.9 5.9 10.1", pathLength: 100 });
    // At a handoff only the words turn over; the mark stays and opens from its result back into a turning arc.
    add(row, mark, el("span", "ck-run-words"));
    if (step.state === "running") { runWords(row, step); } else { doneWords(row, step); }
    return row;
  }
  /** A duration as a running clock with its units: 45s, 4m 08s, 1h 02m 05s; seconds keep two digits past a minute so it holds still as it ticks. */
  function clockTime(spent) {
    var total = Math.max(0, Math.floor(spent)), h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, sec = total % 60;
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return h ? h + "h " + pad(m) + "m " + pad(sec) + "s" : m ? m + "m " + pad(sec) + "s" : sec + "s";
  }
  /**
   * The running check's words: its name, and at the right its time so far as a clock that ticks each second, with the
   * usual time after it. Past the usual time the clock turns wait. A screen reader hears the whole as one sentence.
   */
  function runWords(row, step) {
    var began = seconds(step.startedAt);
    var expected = typeof step.expectedSeconds === "number" ? step.expectedSeconds : null;
    var words = row.querySelector(".ck-run-words");
    var said = el("span", "sr");
    var name = el("span", "ck-run-name", stepName(step));
    var clock = el("b");
    var time = add(el("span", "ck-run-time"), clock, expected !== null ? el("i", null, " / ~" + clockTime(expected)) : null);
    name.setAttribute("aria-hidden", "true");
    time.setAttribute("aria-hidden", "true");
    words.replaceChildren(said, name, time);
    // The spoken sentence is set when drawn and again only when the clock passes the usual time, so a screen reader is
    // not read a new time every second; the clock itself ticks each second.
    var wasOver = null;
    function tick() {
      var spent = began === null ? null : Date.now() / 1000 - began;
      var over = spent !== null && expected !== null && spent > expected;
      row.className = "ck-run" + (over ? " over" : "");
      clock.textContent = spent !== null && spent >= 0 ? clockTime(spent) : "";
      // The run line's label for the running check keeps the same time as the clock beside its name.
      var sheet = row.closest ? row.closest(".checking") : null;
      var label = sheet ? [].filter.call(sheet.querySelectorAll(".rl-time.running"), function (node) { return node.getAttribute("data-check") === text(step.label); })[0] : null;
      if (label && clock.textContent) { label.textContent = clock.textContent + " so far"; }
      if (over === wasOver) { return; }
      wasOver = over;
      said.textContent = "Running " + stepName(step) + (spent !== null && spent >= 0
        ? " · " + duration(spent) + (expected !== null ? " of about " + duration(expected) : " so far") : "") + (over ? ", longer than usual" : "");
    }
    tick();
    if (began !== null && typeof setInterval === "function") {
      // Ticks while the row is on the page and still shows this check; a redraw or a handoff stops it.
      var ticking = setInterval(function () {
        if (!row.isConnected || name.parentNode !== words) { clearInterval(ticking); return; }
        tick();
      }, 1000);
    }
  }
  function doneWords(row, step) {
    var from = seconds(step.startedAt), to = seconds(step.finishedAt);
    var took = from !== null && to !== null && to >= from ? to - from : null;
    var failedIt = step.state === "failed" || step.state === "timed_out";
    row.className = "ck-run settled " + (failedIt ? "is-failed" : "is-passed");
    // A pass needs no word beside its tick (it stays for a screen reader); a failure or a timeout says which it was. The
    // time it took sits where its clock ran, at the right.
    var word = STEP_WORDS[step.state] || "finished";
    var words = row.querySelector(".ck-run-words");
    // A check with no readable start or finish shows no time at all, rather than a gap or a placeholder.
    var parts = [add(el("span", "ck-run-name"), stepName(step), failedIt ? " " + word : el("span", "sr", " " + word))];
    if (took !== null) { parts.push(add(el("span", "ck-run-time"), el("span", "sr", " · " + duration(took)), add(el("b"), clockTime(took)))); }
    words.replaceChildren.apply(words, parts);
    var shown = words.querySelector(".ck-run-time b");
    if (shown) { shown.setAttribute("aria-hidden", "true"); }
  }
  /**
   * The handoff when a check finishes: its spinning arc settles into a full ring, its result draws inside it (a check, or a
   * cross for a failure), it holds there a beat, then the row turns over on its horizontal axis and the next running check
   * arrives on the back. The settle and the beat are SETTLE_MS and HOLD_MS, the turn TURN_OUT_MS then TURN_IN_MS; a failed
   * check's entry in the list (.ck-fails li.arriving in the stylesheet) waits for all three. Without motion the row simply
   * shows the next. A redraw while the row turns keeps that row, turning, and the next result waits its turn on it; a
   * result that came sooner after the last than the whole handoff takes gets a handoff shortened to that gap, a quarter
   * of the whole at least, so the row never falls behind the record.
   */
  var SETTLE_MS = 480, HOLD_MS = 900, TURN_OUT_MS = 200, TURN_IN_MS = 300;
  var HANDOFF_MS = SETTLE_MS + HOLD_MS + TURN_OUT_MS + TURN_IN_MS;
  // The row turning now, the run it belongs to, and the results waiting to turn on it.
  var turning = null;
  function handOff(row, finished, next, run, before) {
    if (!runMotionWanted(row)) { if (!next) { doneWords(row, finished); } turning = null; return; }
    var gap = before ? (seconds(finished.finishedAt) - seconds(before.finishedAt)) * 1000 : NaN;
    var scale = gap >= 0 ? Math.max(0.25, Math.min(1, gap / HANDOFF_MS)) : 1;
    var turn = turning && turning.row === row ? turning : (turning = { row: row, run: run, queue: [] });
    turn.busy = true;
    function done() {
      turn.busy = false;
      var queued = turn.queue.shift();
      if (queued && row.isConnected) { handOff(row, queued.finished, queued.next, run, finished); }
      else if (turning === turn) { turning = null; }
    }
    // The row is first drawn as the finished check was last seen: running, its arc still turning.
    row.className = "ck-run";
    row.querySelector(".ck-run-words").replaceChildren(el("span", "sr", "Running "), stepName(finished));
    later(function () {
      row.classList.add("settling");
      setTimeout(function () {
        doneWords(row, finished);
        row.classList.add("landing");
        setTimeout(function () {
          if (!next) { done(); return; }
          // The words roll over: up and away as they fade, the next rolling in from below. The turn stops short of edge-on
          // and the words are gone before it, so they never flatten into a line; the way in eases out with no overshoot.
          var face = row.querySelector(".ck-run-words");
          var out = face.animate([
            { transform: "perspective(400px) translateY(0) rotateX(0deg)", opacity: 1 },
            { transform: "perspective(400px) translateY(-4px) rotateX(70deg)", opacity: 0 }],
            { duration: TURN_OUT_MS * scale, easing: "cubic-bezier(.4, 0, .7, .4)", fill: "forwards" });
          out.onfinish = function () {
            runWords(row, next);
            var back = face.animate([
              { transform: "perspective(400px) translateY(4px) rotateX(-70deg)", opacity: 0 },
              { transform: "perspective(400px) translateY(0) rotateX(0deg)", opacity: 1 }],
              { duration: TURN_IN_MS * scale, easing: "cubic-bezier(.2, .6, .3, 1)" });
            out.cancel();
            back.onfinish = done;
          };
        }, HOLD_MS * scale);
      }, SETTLE_MS * scale);
    });
  }
  /**
   * Eases the checking sheet from a height to the one it has now, after a live update. While it moves it is clipped at
   * its bottom edge only, so nothing shows below the sheet and the shadow around its other sides stays.
   */
  function easeSheet(sheet, from) {
    var to = sheet.offsetHeight;
    if (from === to || !runMotionWanted(sheet)) { return; }
    sheet.style.clipPath = "inset(-48px -48px 0 -48px round 14px)";
    var easing = sheet.animate([{ height: from + "px" }, { height: to + "px" }], { id: "sheet-height", duration: runEaseDuration(from, to), easing: RUN_EASING });
    easing.onfinish = easing.oncancel = function () { sheet.style.clipPath = ""; };
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
        add(box, add(el("div", "ck-h"), el("span", "ck-dot is-ready"), el("span", "ck-h-words", nextNames.length === 1 ? "Ready to be checked" : nextNames.length + " pieces ready to be checked")));
        // Titles are sentences, so more than one reads as a numbered list in the order they go, not a run-on line.
        if (nextNames.length === 1) { add(box, el("div", "ck-t", nextNames[0])); }
        else { add(box, nextNames.reduce(function (ol, name) { return add(ol, el("li", null, name)); }, el("ol", "ck-members"))); }
        add(box, el("div", "ck-s", "Handed over; each is checked and merged in turn, in this order."));
      }
      if (waiting) { add(box, el("div", "ck-s", waiting + (waiting === 1 ? " more is" : " more are") + " waiting for the lead to accept the hand-over.")); }
      return box;
    }
    // Work an agent is doing right now is live, and its marks turn; a failed run is marked failed below.
    box.classList.add("live");
    var batch = batchNow(doc, all);
    if (batch) {
      var landedIn = batch.filter(function (item) { return item.stage === "merged"; }).length;
      add(box, add(el("div", "ck-h"), el("span", "ck-dot"), el("span", "ck-h-words", landedIn ? "Landing together · " + landedIn + " of " + batch.length + " in" : "Being checked together · " + batch.length + " pieces")));
      var members = el("ol", "ck-members");
      batch.forEach(function (item) {
        var li = add(el("li", item.stage === "merged" ? "in" : ""), item.changeRequest ? changeLink(item.changeRequest) : null, " " + titleOf(doc, item));
        if (item.stage === "merged") { li.appendChild(el("span", "ck-in", "landed")); }
        members.appendChild(li);
      });
      add(box, members);
      add(box, el("div", "ck-batch-note", "One test run for all of them; each lands only if it matches what was tested."));
    } else {
      var waitsOnRerun = byId[current.subject] && rerunOf(doc, byId[current.subject]);
      // The piece by its ticket, then one word for what is happening to it.
      var words = add(el("span", "ck-h-words"), waitsOnRerun ? RERUN_WORDS : "Checking");
      if (text(current.subject)) { add(words, " · ", issue(text(current.subject))); }
      add(box, add(el("div", "ck-h"), el("span", "ck-dot"), words));
      add(box, el("div", "ck-t", byId[current.subject] ? titleOf(doc, byId[current.subject]) : text(current.title) || text(current.subject)));
    }
    // The run failed and the queue is re-running the failing checks on the main line, to tell whose failure it is.
    if (isObject(current.baseRerun)) {
      var rerunChecks = list(current.baseRerun.checks).map(text).filter(Boolean);
      var rerunSince = spoken(current.baseRerun.startedAt);
      add(box, el("div", "ck-rerun", "Rerunning " + (rerunChecks.length ? rerunChecks.join(", ") : "the failing checks") +
        " on the main line, to tell whether it fails there too" + (rerunSince ? " · started " + rerunSince : "")));
    }
    // Checks the lead chose never pass for the provider's answer: each member's reason is said beside the run, one
    // line each until opened, after the run's progress, which is what the panel is for.
    var chosenNotes = [];
    // One note says which checks the lead chose by hand; in a batch, one note covers every piece the lead chose for.
    function chosenFold(key, words, body) {
      // The page's shared fold: it slides, clipping its own words, and is rebuilt open when the reader left it open. The
      // sheet's height is taken again once the slide ends, so a live redraw does not replay it from the closed height.
      var fold = runFold(add(el("details", "ck-chosen"), el("summary", null, words), body),
        { key: "checking:" + key, oneLine: true, onSettle: function () { prev.checkingHeight = box.offsetHeight; } });
      return fold;
    }
    var chosenItems = (batch || (byId[current.subject] ? [byId[current.subject]] : [])).filter(function (item) {
      return isObject(item.checkSelection) && item.checkSelection.by === "lead";
    });
    if (batch && chosenItems.length) {
      var reasons = el("ul", "ck-chosen-list");
      chosenItems.forEach(function (item) {
        var reason = text(item.checkSelection.reason);
        add(reasons, add(el("li"), add(el("b"), titleOf(doc, item)), reason || null));
      });
      chosenNotes.push(chosenFold("batch:" + chosenItems.map(subjectOf).join(","), "Checks chosen by the lead, not the usual selection · for " +
        (chosenItems.length === batch.length ? (batch.length === 2 ? "both pieces" : "all " + batch.length + " pieces") : chosenItems.length + " of " + batch.length + " pieces"), reasons));
    } else {
      chosenItems.forEach(function (item) {
        var reason = text(item.checkSelection.reason);
        chosenNotes.push(chosenFold(subjectOf(item), "Checks chosen by the lead, not the usual selection" + (reason ? ": " + reason : "")));
      });
    }
    // Under lead selection, a piece the lead chose no checks for runs the binding's fallback: the note names it.
    var fallbackItems = (batch || (byId[current.subject] ? [byId[current.subject]] : [])).filter(function (item) {
      return isObject(item.checkSelection) && item.checkSelection.by === "provider" && list(item.checkSelection.fallback).length > 0;
    });
    if (fallbackItems.length) {
      var fallbackNames = list(fallbackItems[0].checkSelection.fallback).map(function (name) { return text(name); }).join(", ");
      var whose = batch && batch.length > 1 ? (fallbackItems.length === batch.length ? " for every piece" : " for " +
        fallbackItems.map(function (item) { return titleOf(doc, item); }).join(", ")) : "";
      chosenNotes.push(el("p", "ck-fallback", "Checking on the fallback, " + fallbackNames + whose + ": the lead selected no checks"));
    }
    var steps = list(current.steps).filter(function (step) { return isObject(step) && step.label !== "selecting checks"; });
    if (steps.length) {
      var done = steps.filter(function (step) { return DONE[step.state]; }).length;
      // The count at a glance; the sentence below says it in words, so this one is not read out twice.
      var count = add(el("span", "ck-count"), String(done), el("i", null, "/" + steps.length));
      count.setAttribute("aria-hidden", "true");
      var head = box.querySelector(".ck-h");
      head.appendChild(count);
      // Once every check has finished and none failed, nothing is being checked any more: the mark settles into a ring
      // with a tick and the header says so, while the piece waits to land. A batch already landing keeps its own words.
      // Done with no failure and at least one check that passed or was reused; a run of skips alone passed nothing.
      var allPassed = done === steps.length && !steps.some(function (step) { return step.state === "failed" || step.state === "timed_out"; }) &&
        steps.some(function (step) { return step.state === "passed" || step.state === "reused"; });
      if (allPassed && !(batch && batch.some(function (item) { return item.stage === "merged"; }))) {
        head.querySelector(".ck-dot").className = "ck-dot is-done";
        // One piece keeps its ticket beside the words; a batch names how many.
        var said = head.querySelector(".ck-h-words");
        if (batch || !said.firstChild || said.firstChild.nodeType !== 3) { said.textContent = batch ? "All checks passed · " + batch.length + " pieces" : "All checks passed"; }
        else { said.firstChild.data = "All checks passed"; }
      }
      var failedSteps = steps.filter(function (step) { return step.state === "failed" || step.state === "timed_out"; });
      var failed = failedSteps.length;
      // Once a check fails, the piece cannot merge from this run, whatever is still to run.
      if (failed) {
        box.classList.add("failed");
        var left = steps.length - done;
        // A failed batch still lands the pieces ahead of the one that broke it, so it only won't land as one.
        add(box, add(el("div", "ck-verdict"), el("b", null, batch ? "The batch won't land as one." : "Won't merge from this run."),
          el("span", null, " " + failed + " failed" + (left ? " · " + left + " still to run" : "") + (batch ? " · the pieces ahead of the one that broke it can still land" : ""))));
      }
      var line = null;
      // Up to twelve checks read in three groups: in progress (the running row below), then waiting, then completed.
      var groups = null;
      if (steps.length <= 12 && !failed) {
        var group = function (title, members) {
          var row = el("div", "ck-steps");
          members.forEach(function (step) {
            // Each check's mark draws its state; the state's word is there for a screen reader.
            var state = STEP_WORDS[step.state] ? step.state : "pending";
            var chip = add(el("span", "ck-step " + state), el("span", "sr", STEP_WORDS[state] + ": "), stepName(step));
            // A title cut short is there whole to read on pointing at it.
            if (text(step.title).length > TITLE_SHOWN) { chip.setAttribute("title", text(step.title)); }
            row.appendChild(chip);
          });
          return add(el("div", "ck-group"), add(el("div", "ck-group-h"), title, el("span", "ck-group-n", String(members.length))), row);
        };
        var waitingSteps = steps.filter(function (step) { return !DONE[step.state] && step.state !== "running"; });
        var doneSteps = steps.filter(function (step) { return DONE[step.state]; });
        groups = [waitingSteps.length ? group("Waiting", waitingSteps) : null, doneSteps.length ? group("Completed", doneSteps) : null].filter(Boolean);
      } else if ((line = runLine(current, steps))) {
        add(box, line);
      } else {
        // With no check times to place, each check takes an equal stretch, in the same well and marks as the run line.
        // The record's order is not the order they ran, so the strip reads as progress instead: what is done from the
        // left, then what is running (named in its row below), then what is still to run.
        var bar = el("div", "ck-bar");
        var ranked = steps.filter(function (step) { return DONE[step.state]; })
          .concat(steps.filter(function (step) { return step.state === "running"; }))
          .concat(steps.filter(function (step) { return !DONE[step.state] && step.state !== "running"; }));
        ranked.forEach(function (step) { bar.appendChild(el("span", STEP_WORDS[step.state] ? step.state : "pending")); });
        var toRun = ranked.length - done - ranked.filter(function (step) { return step.state === "running"; }).length;
        var foot = add(el("div", "ck-bar-foot"), el("span", null, stripReason(current, steps)), toRun ? el("span", null, toRun + " waiting") : null);
        foot.setAttribute("aria-hidden", "true");
        bar.setAttribute("aria-hidden", "true");
        add(box, add(el("div", "ck-bar-well"), bar, foot));
      }
      if (failed) { add(box, failedChecks(doc, batch ? batch.map(subjectOf) : [current.subject === undefined || current.subject === null ? "" : String(current.subject)], current, failedSteps)); }
      // The check shown running at the last draw: when it has finished since, its row settles, marks its result and flips
      // over to the check now running, or rests on its result when none is.
      var runId = text(current.startedAt) + "|" + text(current.subject);
      var runningSteps = steps.filter(function (step) { return step.state === "running"; });
      var shownBefore = prev.runningCheck && prev.runningCheck.run === runId ? prev.runningCheck.label : null;
      var finished = shownBefore && !runningSteps.some(function (step) { return text(step.label) === shownBefore; })
        ? steps.filter(function (step) { return text(step.label) === shownBefore && DONE[step.state]; })[0] : null;
      prev.runningCheck = runningSteps.length ? { run: runId, label: text(runningSteps[0].label) } : null;
      // A row still turning over from an earlier result is kept, turning, and this result waits its turn on it: only the
      // latest waits, so the row turns at most once more before it shows the record as it stands.
      var keep = turning && turning.run === runId && turning.busy ? turning : null;
      if (turning && turning.run !== runId) { turning = null; }
      runningSteps.forEach(function (step, i) {
        if (i === 0 && keep) {
          box.appendChild(keep.row);
          if (finished) { keep.queue = [{ finished: finished, next: step }]; }
          return;
        }
        var row = runRow(step);
        box.appendChild(row);
        if (i === 0 && finished) { handOff(row, finished, step, runId); }
      });
      // A check that has just failed is told once at a time: its row marks the cross and turns over to the next check,
      // and only then does its entry arrive in the failures above. With no next check its entry alone tells it.
      var justFailed = finished && (finished.state === "failed" || finished.state === "timed_out");
      // Under a kept row the cross waits its turn, so the entry arrives as it does with no motion rather than on a delay
      // the row may not keep.
      if (justFailed && runningSteps.length && !keep && runMotionWanted(box)) {
        var arriving = [].filter.call(box.querySelectorAll(".ck-fails li"), function (entry) { return entry.getAttribute("data-check") === text(finished.label); })[0];
        if (arriving) { arriving.classList.add("arriving"); }
      }
      if (keep && !runningSteps.length && !justFailed) {
        box.appendChild(keep.row);
        if (finished) { keep.queue = [{ finished: finished, next: null }]; }
      } else if (keep && !runningSteps.length) {
        // A failure with nothing after it is told by its entry alone, as it is with no row turning: the kept row goes.
        turning = null;
      } else if (finished && !runningSteps.length && !justFailed) {
        var rest = runRow(finished);
        box.appendChild(rest);
        handOff(rest, finished, null, runId);
      }
      var running = steps.filter(function (step) { return step.state === "running"; }).length;
      var tally = function (state) { return steps.filter(function (step) { return step.state === state; }).length; };
      var counts = failed
        ? [tally("passed") + " passed", tally("reused") ? tally("reused") + " reused" : "", tally("skipped") ? tally("skipped") + " skipped" : "", failed + " failed",
          running ? running + " running" : "", steps.length - done - running ? steps.length - done - running + " waiting" : ""].filter(Boolean).join(", ")
        : done + " of " + steps.length + " checks done";
      var began = ago(current.startedAt) ? " · started " + ago(current.startedAt) : "";
      if (groups && groups.length) {
        // The running checks are the sheet's rows; what waits and what is done rest in one line, opened to read by name.
        var waiting = steps.length - done - running;
        var rest = el("div", "ck-rest");
        groups.forEach(function (node) { rest.appendChild(node); });
        // The same fold as the lead's note below it, so both open and close alike.
        add(box, chosenFold("rest:" + runId, [done ? done + " done" : "", waiting ? waiting + " waiting" : ""].filter(Boolean).join(", ") + began, rest));
      } else {
        add(box, el("div", "ck-s", counts + began));
      }
    } else if (ago(current.startedAt)) {
      add(box, el("div", "ck-s", "Started " + ago(current.startedAt)));
    }
    chosenNotes.forEach(function (fold) { add(box, fold); });
    if (nextNames.length) { add(box, el("div", "ck-s ck-next", "Up next: " + nextNames.join(" · "))); }
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
   * The check run as capsules on the clock: each check runs from where it started to where it finished, so a long
   * one is a long capsule, and checks that overlap stack into rows below. A hairline marks now; a failed capsule is
   * numbered above its end as the list below it numbers it, and the waiting checks trail off as rings at the right.
   * Null when the record has no check times to place, or lacks one for a check that ran.
   */
  /** Why the strip stands in for the run line, in the words of its foot. */
  function stripReason(current, steps) {
    var start = seconds(current.startedAt);
    if (start !== null && start >= Date.now() / 1000) { return "the run's clock is ahead of this one"; }
    if (start === null && steps.every(function (step) { return !RAN[step.state] || seconds(step.startedAt) !== null; })) {
      return "no start recorded for the run";
    }
    return steps.some(function (step) { return seconds(step.startedAt) !== null; }) ? "times missing for some checks" : "no times recorded";
  }
  function runLine(current, steps) {
    var start = seconds(current.startedAt);
    var now = Date.now() / 1000;
    var timed = steps.filter(function (step) { return seconds(step.startedAt) !== null; });
    // Every check that ran needs a time to place it: with only some timed, the line would show those few crowded at now
    // and leave the rest out, so the strip below, in order of progress, is drawn instead. A reused or skipped check
    // never ran here, so the runner gives it no time and it needs none.
    var untimed = steps.some(function (step) { return RAN[step.state] && seconds(step.startedAt) === null; });
    if (start === null || now <= start || !timed.length || untimed) { return null; }
    var waiting = steps.filter(function (step) { return !DONE[step.state] && step.state !== "running"; }).length;
    // Drawn at the width it is shown at, so its words stay their size on a phone.
    var width = Math.round(Math.max(300, Math.min(560, typeof innerWidth === "number" && innerWidth > 0 ? innerWidth - 80 : 560)));
    var left = 6, right = width - (waiting ? Math.min(92, Math.round(width * 0.22)) : 6);
    var x = function (t) { return left + Math.max(0, Math.min(1, (t - start) / (now - start))) * (right - left); };
    var kind = function (step) { return step.state === "failed" || step.state === "timed_out" ? "failed" : step.state === "running" ? "running" : "passed"; };
    var spans = timed.map(function (step) {
      var from = seconds(step.startedAt), to = step.state === "running" ? now : seconds(step.finishedAt);
      // A clock behind the runner's can put an end before its start, or a start after now: such a check is a point.
      // An end past now is clamped the same way, so checks the runner stamped ahead of this clock sit together at now
      // instead of each taking a row of its own.
      from = Math.min(from, now);
      return { step: step, kind: kind(step), from: from, to: to === null ? from : Math.min(now, Math.max(from, to)) };
    });
    // Each check is a capsule on the run's clock. Checks that overlap stack into rows, each taking the first row free
    // when it starts, so a long check running beside short ones never hides them.
    var rowEnds = [];
    spans.slice().sort(function (a, b) { return a.from - b.from; }).forEach(function (span) {
      var row = rowEnds.findIndex(function (end) { return end <= span.from; });
      if (row < 0) { row = rowEnds.length; rowEnds.push(span.to); } else { rowEnds[row] = span.to; }
      span.row = row;
    });
    var ROW = 10, TOP = 24, axis = TOP + rowEnds.length * ROW + 18;
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "run-line");
    svg.setAttribute("viewBox", "0 0 " + width + " " + (axis + 4));
    svg.setAttribute("role", "img");
    // Where now is on the clock: a hairline down through the rows.
    shape(svg, "line", { class: "rl-now", x1: right, x2: right, y1: TOP - 6, y2: TOP + rowEnds.length * ROW });
    var longest = Math.max.apply(null, spans.map(function (span) { return span.to - span.from; }).concat([1]));
    var failures = steps.filter(function (step) { return kind(step) === "failed"; });
    // Failures close together keep their numbers apart: each sits above its capsule's end, or just after the number before
    // it on the same row, and never past the drawing's edge.
    var numbered = spans.filter(function (span) { return span.kind === "failed"; }).sort(function (a, b) { return a.to - b.to; });
    var lastNumber = {};
    numbered.forEach(function (span) {
      var at = Math.max(x(span.from), x(span.to)) - 3;
      span.numberAt = Math.max(at, left + 4, lastNumber[span.row] === undefined ? -Infinity : lastNumber[span.row] + 10);
      lastNumber[span.row] = span.numberAt;
    });
    // Then back from the edge: a number pushed past it moves left, and pushes the ones before it left in turn.
    var nextNumber = {};
    numbered.slice().reverse().forEach(function (span) {
      span.numberAt = Math.min(span.numberAt, width - 4, nextNumber[span.row] === undefined ? Infinity : nextNumber[span.row] - 10);
      nextNumber[span.row] = span.numberAt;
    });
    spans.forEach(function (span) {
      var from = x(span.from), to = Math.max(x(span.from), x(span.to)), y = TOP + span.row * ROW;
      var took = span.to - span.from;
      // Passed checks are quiet, the long ones a little less so; the running one is solid; a failure is held.
      var long = span.kind === "passed" && took >= Math.max(60, longest * 0.25);
      // A hair of space between checks that follow one another, so each reads as its own capsule.
      var inset = to - from > 8 ? 1 : 0;
      var seg = shape(svg, "rect", { class: "rl-seg " + span.kind + (long ? " long" : ""), x: from + inset, y: y, width: Math.max(4, to - from - 2 * inset), height: 6, rx: 3 });
      // Pointing at a capsule shows the page's card with its check, its time and how it ended; one on a lower row also
      // names the longest check it ran alongside, which is why it sits below.
      var beside = span.row ? spans.filter(function (other) { return other.row < span.row && other.from < span.to && other.to > span.from; })
        .sort(function (a, b) { return (b.to - b.from) - (a.to - a.from); })[0] : null;
      seg.addEventListener("mouseenter", function () {
        card.replaceChildren(el("b", null, stepName(span.step)),
          el("div", "ic-s", clockTime(span.kind === "running" ? Date.now() / 1000 - span.from : took) + (span.kind === "running" ? " so far · running" :
            " · " + (STEP_WORDS[span.step.state] || "finished"))));
        if (beside) { card.appendChild(el("div", "ic-s", "Ran alongside " + stepName(beside.step))); }
        placeCard(seg);
      });
      seg.addEventListener("mouseleave", hideCard);
      if (span.kind === "running") {
        shape(svg, "text", { class: "rl-time running", x: Math.max(left + 44, to), y: TOP - 10, "text-anchor": "end", "data-check": text(span.step.label) }, clockTime(took) + " so far");
      } else if (span.kind === "failed") {
        // Numbered by place among all the failed checks, as the list below is, even if one has no time to draw.
        var number = failures.indexOf(span.step) + 1;
        shape(svg, "text", { class: "rl-num", x: span.numberAt, y: y - 3, "text-anchor": "middle" }, String(number));
      }
    });
    if (waiting) {
      var dots = Math.min(4, waiting), gap = (width - 10 - (right + 14)) / Math.max(1, dots - 1 || 1);
      for (var i = 0; i < dots; i += 1) { shape(svg, "circle", { class: "rl-wait", cx: right + 14 + i * gap, cy: TOP + 3, r: 2.5 }); }
      shape(svg, "text", { class: "rl-tick", x: width - 4, y: axis, "text-anchor": "end" }, waiting + " waiting");
    }
    shape(svg, "text", { class: "rl-tick", x: left, y: axis }, clock(current.startedAt));
    shape(svg, "text", { class: "rl-tick", x: right, y: axis, "text-anchor": waiting ? "middle" : "end" }, "now");
    var failedCount = spans.filter(function (span) { return span.kind === "failed"; }).length;
    svg.setAttribute("aria-label", "Check run from " + clock(current.startedAt) + " to now: " + timed.length + " started" +
      (failedCount ? ", " + failedCount + " failed" : "") + (waiting ? ", " + waiting + " waiting" : ""));
    return svg;
  }
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
    // The failures journaled for each check, by the check they name, so a note with facts is found by what it was caused by.
    var failedBy = Object.create(null);
    list(doc.journal).forEach(function (entry) {
      if (isObject(entry) && entry.kind === "check.failed" && isObject(entry.data) && typeof entry.data.check === "string") { failedBy[String(entry.id)] = entry.data.check; }
    });
    // Whether a note is about a check: it names the check's failure among its causes.
    function about(entry, label) {
      if (!label) { return false; }
      return list(entry.because).some(function (id) { return failedBy[String(id)] === label; });
    }
    function note(entry, label) {
      used.push(entry);
      // In the story's words: what the lead said, and why.
      var said = entryWords(doc, entry);
      var words = said.head + (said.line ? ". " + said.line : "");
      return add(el("div", "ck-note"), glyph(entry.kind === "blocker.recorded" ? "stop" : "decide"), add(el("span"), words));
    }
    var ul = el("ul", "ck-fails");
    failedSteps.forEach(function (step) {
      var label = text(step.label);
      var record = records[label] || {};
      // Laid out like a "Needs you" row: a small mark, the check, then what is known about it.
      // Numbered as its stop on the run line above.
      var mark = el("span", "g num", String(failedSteps.indexOf(step) + 1));
      mark.setAttribute("aria-hidden", "true");
      // What failed and how much, on one line; then which tests; then what the lane found; the log last, quietest.
      var shownName = checkTitle(step.title) || checkTitle(record.title) || label;
      var head = add(el("div", "ck-fh"), add(el("div", "ck-f"), el("span", "sr", step.state === "timed_out" ? "Timed out: " : "Failed: "), shownName));
      if (text(record.detail)) { head.appendChild(el("div", "ck-d", text(record.detail))); }
      var body = add(el("div", "ck-fb"), head);
      var tests = list(record.failures).map(text).filter(Boolean);
      if (tests.length) { body.appendChild(add(el("div", "ck-tests"), el("span", "ck-k", "Failing: "), tests.join(", "))); }
      notes.filter(function (entry) { return about(entry, label); }).forEach(function (entry) { body.appendChild(note(entry, label)); });
      if (text(record.log)) { body.appendChild(add(el("div", "ck-log"), el("span", "ck-k", "Log: "), text(record.log))); }
      var entry = add(el("li", step.state === "timed_out" ? "timed_out" : "failed"), mark, body);
      entry.setAttribute("data-check", label);
      ul.appendChild(entry);
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

  /**
   * The supervisor's "Needs you": what holds the close first, then what is only shown, each with where it comes from
   * and what clears it. A list from a supervisor that isn't running, one past its staleAfter (a pass is overdue), or
   * one that can't be read is never shown: the box says which instead. The close is journaled before the supervisor
   * stops, so a closed delivery shows the box only while a running, current list still holds it, and otherwise has none:
   * a stopped supervisor is what a finished close looks like, and asking to resume it would be wrong.
   */
  /**
   * A close that stopped partway: the record's started mark for the closing entry with no finished mark for it, and no
   * supervisor running a current pass to finish it. It names what finishes it: a resumed supervisor when the record
   * meets the close condition, and lead-close otherwise, since a resumed supervisor would hold on what is unmet. A
   * record with no marks, or marks for an earlier close, closed as before.
   */
  function closeStoppedPartway(doc, closed) {
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var closing = isObject(meta.closing) ? meta.closing : null;
    if (!closed || !closing || !isObject(closing.started) || closing.started.entry !== closed.id) { return null; }
    if (isObject(closing.finished) && closing.finished.entry === closed.id) { return null; }
    var current = NEEDS !== null ? seconds(NEEDS.staleAfter) : null;
    if (NEEDS !== null && NEEDS.running === true && current !== null && Date.now() / 1000 <= current) { return null; }
    var state = doc.state;
    // As the close reads it: a piece the plan removed holds nothing.
    var kept = function (entry) { return isObject(entry) && !removedFromPlan({ item: entry }); };
    var met = !list(state.planned).some(function (entry) { return kept(entry) && entry.stage !== "settled"; })
      && list(state.queue).filter(kept).every(function (item) { return item.stage === "merged"; })
      && list(state.finish).filter(isObject).every(function (line) { return line.met === true; })
      && list(state.lanes).filter(isObject).every(function (lane) { return lane.state === "idle" || lane.state === "done"; });
    var box = add(el("div", "attention close-partway"), add(el("div", "ah"), persona("supervisor"), "The close stopped partway"));
    return add(box, el("p", "needs-none", "The delivery was closed, but its close-out stopped before it finished. " + (met
      ? "Finish it with lead-run --resume."
      : "Finish it with lead-close, which doesn't wait for the tracker; a resumed supervisor would hold on what is still open.")));
  }
  // Keys that all share a prefix such as "V26-" show without it on the line and beside a "Needs you" row.
  function keyPrefix(all) {
    var keys = all.map(function (piece) { return subjectOf(piece.item); });
    var shared = /^(.*-)[^-]+$/.exec(keys[0] || "");
    return shared && keys.length > 1 && keys.every(function (key) { return key.indexOf(shared[1]) === 0 && key.length > shared[1].length; }) ? shared[1] : "";
  }
  /**
   * What a closed delivery left open: a stopped piece, a broken base, a source's report. A closed page is the
   * delivery's record, so it keeps them under words that ask nothing of anyone, drawn like a "Needs you" row with no
   * meta line, every word the renderer's from the table of wait sentences.
   */
  function leftOpen(doc, all) {
    if (!isObject(LEFT_OPEN) || !list(LEFT_OPEN.rows).filter(isObject).length) { return null; }
    var box = add(el("div", "attention left-open"), add(el("div", "ah"), text(LEFT_OPEN.heading)));
    var ul = el("ul", "needs-rows");
    list(LEFT_OPEN.rows).filter(isObject).forEach(function (row) {
      var said = isObject(row.said) ? row.said : {};
      ul.appendChild(add(el("li", "needs-row is-quiet"), needsMargin(doc, all, row),
        add(el("div", "nr-body"), el("div", "a1", text(said.headline)), text(said.why) ? el("div", "a2", text(said.why)) : null)));
    });
    return add(box, ul);
  }
  // A row's margin: a faint stroke of its lane's line, the story's stop as the line draws it with its short key
  // beneath, and for a wait the blocker's stop in its own lane's colour, joined by a hairline. Hovering a stop names
  // its lane, story and state.
  function needsMargin(doc, all, item) {
    var names = laneNames(doc);
    var bySubject = Object.create(null);
    all.forEach(function (piece) { bySubject[subjectOf(piece.item)] = piece; });
    var prefix = keyPrefix(all);
    var current = checkingNow(doc);
    var nowSubject = current && current.subject !== undefined && current.subject !== null ? String(current.subject) : null;
    function stop(subject, lane, state) {
      var piece = bySubject[subject] || { item: { subject: subject, stage: text(state), lane: lane }, planned: false };
      var kind = stopKind(piece, nowSubject);
      var lineLane = text(lane) || text(piece.item.lane);
      var mark = el("span", "nr-stop");
      mark.style.setProperty("--c", laneColour(lineLane));
      mark.setAttribute("title", [names[lineLane] || lineLane, subject, stageWords(piece.item)].filter(Boolean).join(" · "));
      add(mark, el("span", "stop-btn " + kind), el("span", "nr-key", prefix && subject.indexOf(prefix) === 0 ? subject.slice(prefix.length) : subject));
      return mark;
    }
    var margin = el("div", "nr-mark");
    margin.style.setProperty("--c", laneColour(text(item.lane)));
    margin.appendChild(el("span", "nr-stroke"));
    if (text(item.subject)) { margin.appendChild(stop(text(item.subject), text(item.lane), item.state)); }
    if (isObject(item.blocker) && text(item.blocker.subject)) {
      margin.appendChild(el("span", "nr-hair"));
      margin.appendChild(stop(text(item.blocker.subject), text(item.blocker.lane), item.blocker.state));
    }
    return margin;
  }
  function needsYou(doc, all, closed) {
    if (NEEDS === null) { return null; }
    if (closed) {
      var current = seconds(NEEDS.staleAfter);
      var holding = NEEDS.running === true && current !== null && Date.now() / 1000 <= current
        && list(NEEDS.items).filter(isObject).some(function (item) { return item.holding === true; });
      if (!holding) { return null; }
    }
    var words = isObject(NEEDS.words) ? NEEDS.words : {};
    var box = add(el("div", "needs-you"), add(el("div", "ah"), persona("supervisor"), text(words.heading) || "Needs you"));
    function none(said) { return add(box, el("p", "needs-none", said)); }
    if (NEEDS.unreadable === true) { return none("Unavailable: the supervisor's list can't be read, so it isn't shown."); }
    if (NEEDS.running !== true) {
      return none("Unavailable: the supervisor isn't running, so its list isn't shown. lead-run --resume starts it again.");
    }
    var stale = seconds(NEEDS.staleAfter);
    if (stale === null || Date.now() / 1000 > stale) {
      return none("Unavailable: the supervisor's list is from " + (ago(NEEDS.at) || "an unknown time") +
        ", more than a pass old, so it isn't shown. Its supervisor may have stopped.");
    }
    // Every word of a row comes from the list, which the supervisor said from the table of wait sentences; the page
    // only lays it out. Holding rows lead, and within each, the rows the reader acts on.
    var items = list(NEEDS.items).filter(isObject).map(function (item, at) { return { item: item, at: at }; });
    var rank = function (entry) { return (entry.item.holding === true ? 0 : 2) + (entry.item.who === "operator" ? 0 : 1); };
    items.sort(function (a, b) { return rank(a) - rank(b) || a.at - b.at; });
    if (text(NEEDS.summary)) { box.appendChild(el("p", "needs-summary", text(NEEDS.summary))); }
    if (!items.length) { return box; }
    var ul = el("ul", "needs-rows");
    items.forEach(function (entry) {
      var item = entry.item, said = isObject(item.said) ? item.said : {};
      var li = el("li", "needs-row " + (item.holding === true ? "is-holding" : "is-quiet"));
      var margin = needsMargin(doc, all, item);
      var meta = add(el("div", "nr-meta"), el("span", "nr-who", text(said.who)));
      if (item.holding === true) { add(meta, add(el("span", "nr-holding"), el("span", "nr-dot"), text(words.holding))); }
      if (text(said.clears)) { add(meta, el("span", "nr-clears", text(said.clears))); }
      var body = add(el("div", "nr-body"), el("div", "a1", text(said.headline) || text(item.text)),
        text(said.why) ? el("div", "a2", text(said.why)) : null, meta);
      var age = el("span", "nr-age");
      var since = seconds(item.since);
      if (since !== null && Date.now() / 1000 >= since && text(words.age)) {
        age.textContent = text(words.age).replace("{age}", duration(Date.now() / 1000 - since));
        age.setAttribute("title", clock(item.since));
      }
      ul.appendChild(add(li, margin, body, age));
    });
    box.appendChild(ul);
    if (typeof NEEDS.more === "number" && NEEDS.more > 0) { box.appendChild(el("p", "needs-more", NEEDS.more + " more not shown.")); }
    return box;
  }

  function journey(doc, all, landed) {
    var finish = list(doc.state.finish).filter(isObject);
    var met = finish.filter(function (line) { return line.met === true; }).length;
    var meant = closedEntry(doc) ? closedTally(all).total : all.length;
    var total = meant + finish.length;
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
      ? [["Start", 0, "start"], ["Pieces landed", total ? meant / total : 1, "pieces"], ["Finish line met", 1, "end"]]
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

  function since(doc, meta, closed) {
    // A closed delivery isn't expected to update, so silence isn't a warning; losing the page's connection still is.
    if (closed && (connection === "live" || connection === "connected" || connection === "saved")) {
      var closedAt = seconds(eventTime(closed));
      // An unreadable time, or one ahead of this clock, has no honest "ago".
      var words = closedAt !== null && closedAt <= Date.now() / 1000 ? "Closed " + spoken(eventTime(closed)) : "Closed";
      // The record's first entry is the start; a journal that no longer holds it has no honest total.
      var first = list(doc.journal).filter(isObject)[0];
      var startedAt = first && first.id === 1 ? seconds(first.at) : null;
      var took = closedAt !== null && startedAt !== null && closedAt >= startedAt ? spokenDuration(closedAt - startedAt) : "";
      var when = words === "Closed" ? "" : words.replace(/^Closed /, "");
      var said = [when, took ? (when ? "after " : "took ") + took : ""].filter(Boolean).join(", ");
      var head = null;
      if (meta.baseHead) {
        // Where the work ended up: the main line's last commit, opened on the forge when the renderer gave its address.
        var href = commitHref(meta.baseHead);
        var sha = el(href ? "a" : "span", href ? "issue-link head" : "head", short(meta.baseHead));
        if (href) {
          sha.setAttribute("href", href);
          sha.setAttribute("target", "_blank");
          sha.setAttribute("rel", "noopener noreferrer");
          sha.setAttribute("aria-label", "Open the final commit " + short(meta.baseHead));
        }
        head = add(el("span"), "final commit ", sha);
      }
      return fact("since closed", add(el("span"), el("i"), "Closed"), said || (head ? head : "Nothing more will land"), said ? head : null);
    }
    var updated = seconds(meta.updatedAt);
    var stale = updated !== null && Date.now() / 1000 - updated > 90 * 60;
    var cls = connection === "live" || connection === "connected" ? "live" : connection === "saved" ? "" : "down";
    var words = { live: "Live", connected: "Connected, waiting for updates", down: "Reconnecting. Showing the last update.",
      unreadable: "Live record unreadable. Showing the saved view.", failed: "Live, but the latest update could not be shown", saved: "Saved view" };
    var moved = updated === null ? "" : stale ? "No update for " + ago(meta.updatedAt).replace(" ago", "") : "Updated " + spoken(meta.updatedAt);
    var base = meta.baseHead ? add(el("span"), "base ", el("span", "head", short(meta.baseHead))) : null;
    return fact("since " + (stale ? "stale" : cls), add(el("span"), el("i"), words[connection] || words.saved), moved || base || "No update recorded",
      moved ? base : null);
  }

  /* 2. The line */
  // The lanes' row geometry, matched by the stylesheet: each row's stops centre this far below its top, rows this far apart.
  var LINE_STOP_Y = 23, LINE_PITCH = 84, MERGE_WIDTH = 176;
  /**
   * Beside the lanes, each lane's line turns into one main line that runs to the finish line. A lane's turn is solid
   * once it has landed everything; the main line is solid once every piece is in. Drawn to the rows' fixed geometry.
   */
  function mergeDrawing(doc, all, ends) {
    var height = (ends.length - 1) * LINE_PITCH + LINE_STOP_Y * 2;
    var trunkY = LINE_STOP_Y + (ends.length - 1) * LINE_PITCH / 2;
    var joinX = 100, finishX = MERGE_WIDTH - 14;
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "line-merge");
    svg.setAttribute("viewBox", "0 0 " + MERGE_WIDTH + " " + height);
    svg.setAttribute("width", String(MERGE_WIDTH));
    svg.setAttribute("height", String(height));
    var landed = all.filter(isLanded).length;
    var finish = list(doc.state.finish).filter(isObject);
    var met = finish.filter(function (line) { return line.met === true; }).length;
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Every lane merges into one main line: " + landed + " of " + all.length + " pieces landed" +
      (finish.length ? ", " + met + " of " + finish.length + " finish lines met" : ""));
    shape(svg, "path", { class: "mg-trunk" + (landed === all.length ? "" : " open"), d: "M" + joinX + " " + trunkY + "H" + finishX });
    ends.forEach(function (end, i) {
      var y = LINE_STOP_Y + i * LINE_PITCH;
      var turn = shape(svg, "path", { class: "mg-turn" + (end.done ? "" : " open"),
        d: "M0 " + y + "H12C" + (12 + 52) + " " + y + " " + (joinX - 52) + " " + trunkY + " " + joinX + " " + trunkY });
      turn.style.setProperty("--c", end.colour);
    });
    shape(svg, "circle", { class: "mg-join", cx: joinX, cy: trunkY, r: 4 });
    shape(svg, "path", { class: "mg-post", d: "M" + finishX + " " + (trunkY - 20) + "V" + (trunkY + 20) });
    shape(svg, "text", { class: "mg-label", x: finishX + 3, y: trunkY - 30, "text-anchor": "end" }, "Finish line");
    // The totals stack under the post, short enough to stay clear of the lanes turning in.
    figureText(svg, "merge-landed", { class: "mg-count", x: finishX + 3, y: trunkY + 38, "text-anchor": "end" }, landed + "/" + all.length + " landed");
    if (finish.length) { figureText(svg, "merge-met", { class: "mg-count", x: finishX + 3, y: trunkY + 52, "text-anchor": "end" }, met + "/" + finish.length + " met"); }
    return svg;
  }
  /**
   * The line in motion, while the delivery is open. What has landed is still. A piece being built, reviewed or checked
   * has its lane drawn solid up to its stop and no further. The piece being checked fires packets from
   * its stop, along its lane, through the turn and down the main line toward the finish line, as one steady stream that
   * is fuller the more checks are running: the run is trying the way the piece will take. Nothing here says anything the stops and the checking sheet
   * don't, so it is hidden from assistive technology, and it is not drawn at all for a reader who asked for less motion
   * or once the delivery has closed.
   */
  // The stream keeps the page's beat (--beat in the stylesheet): a swell is a whole number of beats long.
  var FLOW_SPEED = 72, BEAT_MS = 1200;
  function drawFlow(track, flows, laneCount, running) {
    var old = track.querySelector(".line-flow");
    if (old) { old.remove(); }
    var width = track.clientWidth, height = track.clientHeight;
    if (!flows.length || !width || reduced() || typeof track.getBoundingClientRect !== "function") { return; }
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "line-flow");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("aria-hidden", "true");
    track.appendChild(svg);
    if (typeof svg.animate !== "function") { svg.remove(); return; }
    var origin = track.getBoundingClientRect();
    var top = parseFloat(getComputedStyle(track).paddingTop) || 0;
    var mergeX = width - MERGE_WIDTH, joinX = mergeX + 100, finishX = mergeX + MERGE_WIDTH - 14;
    var trunkY = top + LINE_STOP_Y + (laneCount - 1) * LINE_PITCH / 2;
    // One unbroken stream: a swell of light runs down it at an even pace, with no gap anywhere along it. More running
    // checks bring the swells closer, never faster. Each swell builds slowly and ends at a bright head, so the eye reads
    // which way it runs.
    var beats = Math.max(2, 5 - Math.max(1, running));
    var wave = Math.round(FLOW_SPEED * BEAT_MS / 1000 * beats);
    var SWELL = [[0, .12], [.4, .2], [.8, .78], [.9, .78], [1, .12]];
    function stream(id, cls, colour, d, from, to, fadeStops) {
      var light = shape(svg, "linearGradient", { id: id + "-light", gradientUnits: "userSpaceOnUse", spreadMethod: "repeat", x1: 0, y1: 0, x2: wave, y2: 0 });
      SWELL.forEach(function (stop) { shape(light, "stop", { class: cls + "-light", offset: stop[0], "stop-opacity": stop[1] }).style.setProperty("--c", colour); });
      // Run from the document's own clock, not from this draw, so a live redraw carries on without a jump; every stream
      // shares the one pace and spacing, so a swell passes from a lane onto the main line without a break.
      shape(light, "animateTransform", { attributeName: "gradientTransform", type: "translate", from: "0 0", to: wave + " 0", begin: "0s",
        dur: (beats * BEAT_MS / 1000).toFixed(2) + "s", repeatCount: "indefinite" });
      var fade = shape(svg, "linearGradient", { id: id + "-fade", gradientUnits: "userSpaceOnUse", x1: from, y1: 0, x2: to, y2: 0 });
      fadeStops.forEach(function (stop) { shape(fade, "stop", { offset: stop[0], "stop-color": "#fff", "stop-opacity": stop[1] }); });
      var mask = shape(svg, "mask", { id: id + "-mask", maskUnits: "userSpaceOnUse", x: 0, y: 0, width: width, height: height });
      shape(mask, "rect", { x: 0, y: 0, width: width, height: height, fill: "url(#" + id + "-fade)" });
      shape(svg, "path", { class: cls, d: d, stroke: "url(#" + id + "-light)", mask: "url(#" + id + "-mask)" }).style.setProperty("--c", colour);
    }
    var firing = flows.filter(function (flow) { return flow.fires; });
    // On its lane a stream is the lane's colour, rising out of the stop and running to where the lanes join.
    firing.forEach(function (flow) {
      var at = flow.button.getBoundingClientRect();
      var x = at.left + at.width / 2 - origin.left, y = top + LINE_STOP_Y + flow.row * LINE_PITCH;
      stream("flow-" + flow.row, "flow-shot", flow.colour, "M" + x + " " + y + "H" + (mergeX + 12) + "C" + (mergeX + 64) + " " + y + " " + (joinX - 52) + " " +
        trunkY + " " + joinX + " " + trunkY, x, joinX, [[0, 0], [.2, 1], [1, 1]]);
    });
    // Past the join the streams are one, in the main line's own colour, thinning away before the finish line.
    if (firing.length) {
      stream("flow-trunk", "flow-trunk", "", "M" + joinX + " " + trunkY + "H" + finishX, joinX, finishX, [[0, 1], [.45, 1], [1, 0]]);
    }
    flows.forEach(function (flow) {
      var box = flow.button.getBoundingClientRect();
      var to = box.left - origin.left - 5, from = flow.from === null ? flow.left - origin.left : flow.from.getBoundingClientRect().right - origin.left + 5;
      var y = top + LINE_STOP_Y + flow.row * LINE_PITCH;
      if (to - from < 18) { return; }
      var dots = shape(svg, "path", { class: "flow-approach", d: "M" + from + " " + y + "H" + to });
      dots.style.setProperty("--c", flow.colour);
    });
  }
  function drawLine(doc, all) {
    var inner = parts.line;
    var names = laneNames(doc);
    var colours = laneColours(doc, all);
    var current = checkingNow(doc);
    var nowSubject = current && current.subject !== undefined && current.subject !== null ? String(current.subject) : null;
    // The detail panel on the page now is replaced below, empty; a new piece's panel eases from this height, not that one's.
    var shown = document.getElementById("detail");
    var shownHeight = shown ? runHeldHeight(shown) : null;
    var nodes = [el("div", "kicker", "The line"), el("h2", "title", "Every piece of work merges into one main line."),
      el("p", "say", "Each row is one lane's work, in the order it lands. Point at or tap a stop to see what it carries.")];
    if (!all.length) {
      nodes.push(el("p", "waiting", closedEntry(doc) ? "No work reached the line." : "No work on the line yet."));
      inner.replaceChildren.apply(inner, nodes);
      return;
    }
    var lanes = [];
    all.forEach(function (piece) { var lane = piece.item.lane || ""; if (lanes.indexOf(lane) < 0) { lanes.push(lane); } });
    // Every lane's stops sit on one pitch, set by the longest lane, and end together where the lane turns into the main line.
    var most = Math.max.apply(null, lanes.map(function (lane) { return all.filter(function (piece) { return (piece.item.lane || "") === lane; }).length; }));
    var box = el("div", "lines");
    var track = el("div", "lines-track");
    track.style.setProperty("--most", String(most));
    box.appendChild(track);
    var stages = Object.create(null);
    var buttons = [];
    var ends = [];
    // Keys that all share a prefix such as "V26-" show without it on the line, so a lane's stops fit beside the merge; the
    // full key stays the link's accessible name and its data.
    var prefix = keyPrefix(all);
    var flows = [];
    lanes.forEach(function (lane, laneIndex) {
      var items = all.filter(function (piece) { return (piece.item.lane || "") === lane; });
      var row = el("div", "line");
      row.style.setProperty("--c", colours[lane]);
      var landedHere = items.filter(isLanded).length;
      // The lane's rule is solid up to its last landed stop and faint after it; a lane that landed everything is solid to the turn.
      var lastLanded = -1;
      items.forEach(function (piece, i) { if (isLanded(piece)) { lastLanded = i; } });
      var solid = landedHere === items.length ? 100 : lastLanded < 0 ? 0 : 100 - (items.length - 1 - lastLanded + 0.5) * 100 / most;
      ends.push({ colour: colours[lane], done: landedHere === items.length });
      add(row, add(el("div", "name"), el("b", null, lane ? names[lane] || lane : noLane(doc)), figures(el("span"), "lane:" + lane, landedHere + " of " + items.length + " landed")));
      var stops = el("div", "stops");
      var merged = function (k) { return k >= 0 && k < items.length && isLanded(items[k]); };
      items.forEach(function (piece, i) {
        var kind = unlandedAtClose(doc, piece) ? "unlanded" : stopKind(piece, nowSubject);
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
        var waiting = kind === "unlanded" ? [] : waitWords(doc, key);
        button.setAttribute("aria-label", titleOf(doc, piece.item) + ", " + (kind === "unlanded" ? "Not landed" : pieceWords(doc, piece.item)) +
          (waiting.length ? ". " + waiting.join(" ") : ""));
        button.setAttribute("aria-pressed", String(selected === key));
        var show = function () { selected = key; showDetail(doc, piece, colours, nowSubject); };
        button.addEventListener("click", show);
        button.addEventListener("mouseenter", show);
        button.addEventListener("focus", show);
        buttons.push(button);
        if (kind === "active" || kind === "now" || kind === "ready") {
          flows.push({ fires: kind === "now", button: button, row: laneIndex, colour: colours[lane], stops: stops,
            from: i > 0 ? buttons[buttons.length - 2] : null });
        }
        // The change request sits under the ticket once one is open, and opens it.
        var ticket = issue(key, prefix ? key.slice(prefix.length) : key);
        if (prefix) { ticket.setAttribute("aria-label", key); }
        add(seg, button, add(el("div", "stop-label"), ticket, piece.item.changeRequest ? changeLink(piece.item.changeRequest) : null));
        stops.appendChild(seg);
      });
      stops.style.setProperty("--solid", solid + "%");
      add(row, stops);
      track.appendChild(row);
    });
    track.appendChild(mergeDrawing(doc, all, ends));
    var detail = el("div", "detail");
    detail.id = "detail";
    detail.setAttribute("aria-live", "polite");
    var legend = el("div", "line-legend");
    (closedEntry(doc) ? [["f", "Landed"], ["u", "Not landed"], ["link", "Both landed"]]
      : [["f", "Landed"], ["n", "Being checked"], ["r", "Ready to check"], ["a", "In progress"], ["d", "Not started"], ["p", "Waiting for the lead to accept"], ["h", "Stopped"], ["link", "Both landed"]]).forEach(function (entry) {
      legend.appendChild(add(el("span"), el("i", entry[0]), entry[1]));
    });
    nodes.push(box, detail, legend);
    inner.replaceChildren.apply(inner, nodes);
    // Where the line scrolls sideways, as on a phone, it opens at its end, where the lanes merge and the newest work is,
    // and keeps the reader's own place on later redraws.
    // Kept as the distance from the right edge, where the newest work is, so a change of width still shows it.
    if (box.scrollWidth > box.clientWidth) { box.scrollLeft = box.scrollWidth - box.clientWidth - (prev.lineScroll || 0); }
    box.addEventListener("scroll", function () { prev.lineScroll = Math.max(0, box.scrollWidth - box.clientWidth - box.scrollLeft); });
    prev.stages = stages;
    // The motion is measured from the drawn rows, so it is laid once they are in the page, and again when their width changes.
    if (prev.flowWatch) { prev.flowWatch.disconnect(); prev.flowWatch = null; }
    if (!closedEntry(doc)) {
      var layFlow = function () {
        flows.forEach(function (flow) { flow.left = flow.stops.getBoundingClientRect().left; });
        drawFlow(track, flows, lanes.length, current ? list(current.steps).filter(function (step) { return isObject(step) && step.state === "running"; }).length : 0);
      };
      layFlow();
      if (typeof ResizeObserver === "function") { prev.flowWatch = new ResizeObserver(layFlow); prev.flowWatch.observe(track); }
    }
    var pick = all.filter(function (piece) { return subjectOf(piece.item) === selected; })[0] ||
      all.filter(function (piece) { return stopKind(piece, nowSubject) === "now"; })[0] ||
      all.filter(function (piece) { return stopKind(piece, nowSubject) === "held"; })[0] ||
      all.filter(function (piece) { return stopKind(piece, nowSubject) !== "merged"; })[0] || all[0];
    if (pick) { showDetail(doc, pick, colours, nowSubject, shownHeight); }
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
    else if (item.stage === "settled") {
      label = "Closed"; basis = "Recorded acceptance of the no-change hand-off";
      at = isObject(item.noChange) ? text(item.noChange.settledAt) || null : null;
    } else if (item.stage === "review" && isObject(item.noChange)) {
      label = "Handed over"; basis = "Recorded no-change hand-off"; at = text(item.noChange.handedAt) || null;
    }
    else if (item.stage === "review") {
      label = "In review since"; basis = "Recorded review round start";
      event = last("review.round"); at = event && roundOf(event) ? roundOf(event).startedAt : null;
    } else if (item.stage === "queued") {
      label = "Handed over"; basis = "Recorded hand-off"; event = handedIndex >= 0 ? own[handedIndex] : null;
    } else if (item.stage === "checking" && rerunOf(doc, item)) {
      label = "Waiting on the rerun since"; basis = "Recorded base rerun start"; event = last("base.rerun.started", checks);
      at = event ? null : text(rerunOf(doc, item).startedAt) || null;
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
        event = own.filter(function (entry) {
          var recovery = entry.kind === "candidate.merged" && isObject(entry.data) && isObject(entry.data.recovery) ? entry.data.recovery : null;
          return recovery && recovery.head === (item.syncedHead || item.head) && recovery.changeRequest === (item.changeRequest || null); }).pop();
      }
    } else if (item.stage === "merging" || item.stage === "syncing" || item.stage === "synced") {
      label = "Time"; basis = "No durable transition time recorded";
    } else { return null; }
    if (event && item.stage !== "review" && item.stage !== "settled") { at = eventTime(event); }
    return { label: label, basis: basis, at: at };
  }

  /**
   * A redraw passes the height the replaced panel had, `from`, or null on the first draw, which has none to ease from;
   * a click shows a piece in the panel already there.
   */
  function showDetail(doc, piece, colours, nowSubject, from) {
    var detail = document.getElementById("detail");
    if (!detail) { return; }
    var item = piece.item;
    detail.style.setProperty("--c", colours[item.lane || ""] || "var(--soft)");
    var subject = subjectOf(item);
    var switched = prev.selected !== subject;
    if (from === undefined) { from = runHeldHeight(detail); }
    if (switched && !reduced()) { replay(detail, "swap"); }
    prev.selected = subject;
    // The lane that built the piece labels the panel, by the lane's own name: a lane's title follows its current work, so
    // it would name another piece. Under the title, the piece's ticket and change request, then where it is and when it landed.
    var lane = text(item.lane);
    var owner = el("div", "d-owner" + (lane ? "" : " is-none"), lane ? laneLabel(lane) : noLane(doc));
    if (lane) { owner.insertBefore(persona(lane === "lead" ? "lead" : "lane"), owner.firstChild); }
    var line = el("div", "d2 d-meta");
    add(line, issue(subject));
    if (item.changeRequest) { add(line, " · ", changeLink(item.changeRequest)); }
    // Nothing is still being checked or waiting once the delivery has closed; the piece simply didn't land.
    var unlanded = unlandedAtClose(doc, piece);
    var timing = unlanded ? null : stageTime(doc, item);
    // Planned work the tracker has started, building with no assignment recorded, started when its ticket did, and a
    // tracker snapshot carries no start time, so the card names the stage alone rather than call its time unknown.
    if (timing && !timing.at && piece.planned && item.stage === "building" && item.trackerStarted === true) { timing = null; }
    var stage = el("span", "d-stage");
    var icon = unlanded ? null : stageIcon(item);
    if (icon) { stage.appendChild(icon); }
    add(stage, unlanded ? "Not landed" : pieceWords(doc, item));
    if (timing) {
      var relative = spoken(timing.at);
      var when = el("span", "d-stage-time", (item.stage === "merged" || item.stage === "settled" ? (relative ? " " + relative : " · time unknown")
        : " · " + timing.label + (relative ? " " + relative : (timing.label === "Time" ? " unknown" : " · time unknown"))));
      var exact = timing.basis + ": " + (relative ? timing.at : "unknown");
      when.setAttribute("aria-label", exact);
      stage.appendChild(when);
    }
    add(line, " · ", stage);
    var nodes = [owner, el("div", "d1", titleOf(doc, item)), line].concat(unlanded ? [] : waitLines(doc, subject, "div", "d2 d-wait"));
    var inPlan = planStop(doc, subject);
    if (inPlan) { nodes.push(inPlan); }
    var batch = item.stage !== "merged" && !unlanded && batchNow(doc, pieces(doc));
    if (batch && batch.some(function (member) { return member.subject === item.subject; })) {
      nodes.push(el("div", "d-batch", "Checked together with " + batch.filter(function (member) { return member.subject !== item.subject; })
        .map(function (member) { return text(member.subject); }).join(", ") + "; lands in order once the shared test run passes."));
    }
    var tickets = isObject(doc.state.tickets) ? doc.state.tickets : {};
    if (isObject(tickets[item.subject]) && text(tickets[item.subject].summary)) { nodes.push(el("div", "d2", text(tickets[item.subject].summary))); }
    if (item.stage === "stopped") { failures(item).forEach(function (why) { nodes.push(el("div", "d3", why)); }); }
    if (piece.planned && isObject(item.noChange) && text(item.noChange.evidence)) {
      nodes.push(el("div", "d2", "No change needed. Evidence, run at the main line's " + text(item.noChange.baseHead).slice(0, 10)
        + ": " + text(item.noChange.evidence)));
    }
    var leadInputs = list(item.leadInputs).map(text).filter(Boolean);
    if (leadInputs.length && !unlanded) { nodes.push(el("div", "d2", "Needs the lead first: " + leadInputs.join(", "))); }
    var estimate = isObject(doc.estimates) && isObject(doc.estimates[item.subject]) ? doc.estimates[item.subject] : null;
    if (estimate && item.stage === "queued" && !unlanded) {
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

  // The candidate a stop's review evidence must name. A handed stop's is its head. A planned stop has no head before
  // hand-off, so it is the candidate its latest capture names; the record keeps a capture only when it is of the
  // lane's latest journaled round, and the stop's review.round and review.recorded boundary must still end on it.
  function reviewedCandidate(doc, item, evidence) {
    var queued = list(doc.state.queue).some(function (entry) { return isObject(entry) && entry.subject === item.subject; });
    if (queued) { return item.head; }
    return evidence && keptReview(evidence) ? keptReview(evidence).candidateRef : undefined;
  }
  function isPlanned(doc, item) {
    return list(doc.state.planned).some(function (entry) { return isObject(entry) && entry.subject === item.subject; })
      && !list(doc.state.queue).some(function (entry) { return isObject(entry) && entry.subject === item.subject; });
  }

  // The run that built the stop, as the product's run view summarized it when the page was rendered.
  // A round the view gives no lens verdicts for takes them from the record's own review.round entry.
  function runFor(doc, item) {
    /**
     * A story's round in flight: its latest review.started, until a review.round answers it or names its round, the
     * story is handed off, settled or picked up again, or another start follows. Each lens back is its latest
     * review.lens.returned for that round. { round, head, at, lenses, back, why, moved } or null.
     */
    function roundInFlight(doc, subject) {
      var start = journalFor(doc, subject, { "review.started": true }).slice(-1)[0];
      var said = start && isObject(start.data) ? start.data : null;
      if (!said || typeof said.round !== "number" || typeof said.head !== "string" || settledEntry(doc, subject)) { return null; }
      var after = journalFor(doc, subject).filter(function (entry) { return (entry.id || 0) > (start.id || 0); });
      var over = after.some(function (entry) {
        return entry.kind === "candidate.handed" || entry.kind === "lane.assigned" || (entry.kind === "review.round"
          && (list(entry.answers).indexOf(start.id) >= 0 || (isObject(entry.data) && entry.data.round === said.round)));
      });
      if (over) { return null; }
      var back = Object.create(null);
      after.forEach(function (entry) {
        var data = entry.kind === "review.lens.returned" && isObject(entry.data) ? entry.data : null;
        if (data && data.round === said.round && typeof data.lens === "string") { back[data.lens] = { verdict: data.verdict, findings: data.findings, at: entry.at }; }
      });
      // The card's headline is the story's review wait, as the state tool says it; without one the card words its own.
      var waiting = waitsOf(doc, "waits", subject).filter(function (wait) { return wait.cause === "review"; })[0];
      return { round: said.round, head: said.head, at: start.at, lenses: list(said.lenses).filter(function (lens) { return typeof lens === "string"; }),
        back: back, why: text(said.why) || null, moved: typeof said.moved === "number" ? said.moved : null,
        headline: waiting ? waiting.words : null };
    }
    if (typeof item.runId !== "string" || item.runId === "") {
      var native = Object.prototype.hasOwnProperty.call(nativeReviews, item.subject) ? nativeReviews[item.subject] : null;
      var events = journalFor(doc, item.subject, { "review.round": true, "review.recorded": true });
      var inFlight = roundInFlight(doc, item.subject);
      // The round in flight, over the finished rounds a valid observation gives, or over none.
      var underWay = function (finished, note) {
        var box = el("div", "d-run");
        if (note) { box.appendChild(el("div", "d-run-none", note)); }
        var shown = isObject(finished) ? finished : { observational: true, observationKey: item.subject, open: false, roundDetail: [] };
        box.appendChild(runSummaryNode(Object.assign({}, shown, { running: inFlight }), { commitHref: commitHref, issueHref: issueHref, lenses: VIEW.lenses, subject: item.subject }));
        return box;
      };
      if (inFlight && !native && !events.length) { return underWay(null); }
      var boundary = events.reduce(function (last, event) { return Math.max(last, event.id || 0); }, 0);
      var evidence = events.filter(function (event) { return KEPT[event.kind]; }).slice(-1)[0];
      var observation = isObject(doc.reviewEvidence) ? doc.reviewEvidence[item.subject] : undefined;
      if (!native && !events.length) {
        return isPlanned(doc, item) && !closedEntry(doc)
          ? el("div", "d-run-none", "No review rounds yet. Each round shows here as soon as the lane reduces it, before the hand-off.")
          : null;
      }
      var candidate = reviewedCandidate(doc, item, evidence);
      // Evidence the lead carried to a later head covers that head; its rounds still name the head they reviewed.
      var carried = isObject(native) && isObject(native.carried) ? native.carried : null;
      var covers = carried ? carried.to : (isObject(native) ? native.candidateRef : undefined);
      if (!isObject(native) || native.unavailable === true || typeof candidate !== "string" || covers !== candidate
          || native.journalId !== boundary
          || (evidence && (!keptReview(evidence) || keptReview(evidence).digest !== native.digest))
          || (isObject(doc.reviewEvidence) && (!isObject(observation) || observation.unavailable === true || observation.digest !== native.digest))) {
        if (inFlight) { return underWay(null, "Earlier rounds' evidence is unavailable or stale for this candidate."); }
        return el("div", "d-run-none", "Review evidence unavailable or stale for this candidate." + (isPlanned(doc, item) && !closedEntry(doc)
          ? " Its rounds show here again once the lane's review-reduce records its latest round." : ""));
      }
      if (inFlight) { return underWay(native); }
      var nativeBox = el("div", "d-run");
      if (carried) { nativeBox.appendChild(el("div", "d-run-carried", "Carried by the lead. " + text(carried.note))); }
      nativeBox.appendChild(runSummaryNode(native, { commitHref: commitHref, issueHref: issueHref, lenses: VIEW.lenses, subject: item.subject }));
      return nativeBox;
    }
    if (VIEW.runs !== true) { return null; }
    var summary = Object.prototype.hasOwnProperty.call(runs, item.runId) ? runs[item.runId] : null;
    if (!isObject(summary) || summary.unavailable === true) { return el("div", "d-run-none", "Run " + item.runId + " unavailable."); }
    var journaled = {};
    journalFor(doc, item.subject, { "review.round": true }).forEach(function (entry) {
      var round = roundOf(entry);
      if (round && isObject(round.verdicts)) { journaled[round.round] = round.verdicts; }
    });
    // The run's own verdicts stand. A lens the run gives no verdict for, or calls unknown, takes the one the record's
    // review.round entry journaled for that round, and a round the run gives no outcome for takes one from those verdicts.
    var rounds = list(summary.roundDetail).map(function (round) {
      if (!isObject(round) || !isObject(journaled[round.round])) { return round; }
      var given = isObject(round.verdicts) ? round.verdicts : {};
      var recorded = journaled[round.round];
      var verdicts = {};
      Object.keys(given).concat(Object.keys(recorded)).forEach(function (lens) {
        if (Object.prototype.hasOwnProperty.call(verdicts, lens)) { return; }
        var own = given[lens];
        verdicts[lens] = own !== undefined && own !== null && own !== "unknown" ? own : recorded[lens] !== undefined ? recorded[lens] : own;
      });
      var said = Object.keys(verdicts).map(function (lens) { return verdicts[lens]; });
      var outcome = round.outcome !== undefined && round.outcome !== null ? round.outcome
        : !said.length ? round.outcome : said.every(function (verdict) { return verdict === "approve"; }) ? "approve"
        : said.indexOf("changes") >= 0 ? "changes" : round.outcome;
      return Object.assign({}, round, { verdicts: verdicts, outcome: outcome });
    });
    var box = el("div", "d-run");
    box.appendChild(runSummaryNode(Object.assign({}, summary, { roundDetail: rounds }), { commitHref: commitHref, issueHref: issueHref, lenses: VIEW.lenses }));
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
        var listed = /<script type="application\/json" id="delivery-needs">([^<]*)<\/script>/.exec(page);
        NEEDS = listed ? readNeeds(listed[1]) : null;
        var opened = /<script type="application\/json" id="delivery-left-open">([^<]*)<\/script>/.exec(page);
        LEFT_OPEN = opened ? readNeeds(opened[1]) : null;
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

  // The steps of a piece's merge check: which kinds belong to it. What each says, and its label, come from the story.
  var HISTORY = table({ "candidate.handed": true, "check.started": true, "check.finished": true, "check.failed": true, "check.retried": true,
    "check.reused": true, "check.carried": true, "base.rerun.started": true, "base.rerun.finished": true, "base.rerun.stopped": true,
    "candidate.stopped": true, "candidate.merged": true });
  /** One step as the story tells it: its head and line. */
  function stepSaid(doc, entry) {
    var words = entryWords(doc, entry);
    return words.head + (words.line ? ": " + words.line : "");
  }
  function historyFor(doc, item) {
    var rows = journalFor(doc, item.subject, HISTORY);
    if (!rows.length) { return null; }
    var box = el("details", "mh");
    var last = rows[rows.length - 1];
    var summary = el("summary");
    add(summary, "Merge check", el("span", "mh-sub", " · " + (entryWords(doc, last).label || "") + " " + (spoken(eventTime(last)) || "time unknown") + " · " + rows.length + (rows.length === 1 ? " step" : " steps")));
    var ol = el("ol");
    rows.slice(-8).forEach(function (entry) {
      var li = el("li", entry.kind === "candidate.merged" ? "in" : entry.kind === "candidate.stopped" ? "bad" : "");
      var time = el("time", null, clock(eventTime(entry)) || "Time unknown");
      time.setAttribute("datetime", text(eventTime(entry)));
      add(li, time, el("span", null, stepSaid(doc, entry)));
      ol.appendChild(li);
    });
    add(box, summary, ol);
    return runFold(box, { key: "history:" + item.subject });
  }

  /* 3. Who */
  // An hour, the same as a wait's quiet lane: long reviews can be longer, so the mark says may, and only of an active row.
  var ROW_OLD_SECONDS = 3600;
  /** How old a row's words are, marked once they may be out of date; a time that can't be read says so. */
  function rowAge(lane) {
    var at = seconds(lane.focusAt);
    var delta = at === null ? -1 : Date.now() / 1000 - at;
    if (delta < 0) { return el("p", "said", "No time recorded"); }
    var old = delta > ROW_OLD_SECONDS && lane.state !== "idle" && lane.state !== "done";
    return el("p", "said" + (old ? " old" : ""), (delta < 45 ? "Updated just now" : "Updated " + duration(delta) + " ago") + (old ? "; may be out of date" : ""));
  }
  function drawWho(doc, all) {
    var inner = parts.who;
    var lanes = list(doc.state.lanes).filter(isObject);
    var colours = laneColours(doc, all);
    var focus = isObject(doc.focus) ? doc.focus : {};
    var busy = lanes.filter(function (lane) { return lane.state !== "idle" && lane.state !== "done"; }).length;
    var closed = closedEntry(doc);
    var heading = closed ? "This delivery is closed; no lane is working."
      : !lanes.length ? "No one has reported in yet."
      : busy === lanes.length ? lanes.length + (lanes.length === 1 ? " lane is" : " lanes are") + " working on this."
      : busy + " of " + lanes.length + " lanes are working right now.";
    var nodes = [el("div", "kicker", "Who's doing what"), figures(el("h2", "title"), "who", heading)];
    var seen = Object.create(null);
    if (lanes.length) {
      nodes.push(el("p", "say", "Each lane " + (closed ? "was" : "is") + " a separate session working in parallel on its own part of the work."));
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
        if (text(lane.focus) && !closed) { who.appendChild(rowAge(lane)); }
        if (!closed) { waitLines(doc, lane.id, "p", "doing wait").forEach(function (line) { who.appendChild(line); }); }
        var st = add(el("div", "st"), closed ? el("span", "chip done", "Finished") : el("span", "chip" + (LANE_WORDS[lane.state] ? " " + lane.state : ""), LANE_WORDS[lane.state] || "Unknown"));
        if (closed) { /* what a lane last waited on or how long it has held is over */ }
        else if (lane.waitingOn) { st.appendChild(el("span", "on waiting", "waiting on " + text(lane.waitingOn))); }
        else if (heldFor(lane.since)) { st.appendChild(el("span", "on", heldFor(lane.since))); }
        add(row, add(el("span", "swatch"), persona("lane")), who, st);
        people.appendChild(row);
      });
      nodes.push(people);
      prev.lanes = seen;
    }
    var leases = list(doc.state.leases).filter(isObject);
    if (leases.length && !closed) {
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
    var open = all.filter(function (piece) { return !isLanded(piece); });
    var closedOut = closedEntry(doc);
    var tally = closedOut ? closedTally(all) : { landed: all.length - open.length, total: all.length, placed: all.length };
    var left = tally.total - tally.landed;
    var number = add(el("div", "left-number"), figures(el("span"), "left", String(left)),
      el("small", null, closedOut && left ? (left === 1 ? "piece not landed" : "pieces not landed") : left === 1 ? "piece to go" : "pieces to go"));
    var wasOpen = prev.dots || {};
    var nowOpen = Object.create(null);
    // Nothing counts down once a delivery closes with every piece landed.
    var nodes = [el("div", "kicker", "What's left"), closedOut && !left ? el("h2", "title", tally.total || all.length ? "Nothing left to land." : "Nothing was on the line.") : number];
    if (tally.total) { nodes.push(figures(el("p", "say"), "left-say", tally.landed + " of " + tally.total + " pieces have landed.")); }
    else if (!closedOut) { nodes.push(el("p", "say", "Nothing is planned yet.")); }
    if (tally.total) {
      var dots = el("div", "dots");
      dots.setAttribute("aria-hidden", "true");
      var closing = 0;
      all.forEach(function (piece) {
        if (closedOut && removedFromPlan(piece)) { return; }
        var key = text(piece.item.subject);
        var isOpen = !isLanded(piece);
        nowOpen[key] = isOpen;
        // A dot that just closed lights up in its lane's colour, then settles to the landed green.
        var closed = !isOpen && wasOpen[key] === true && !reduced();
        var dot = el("i", isOpen ? "open" : closed ? "closing" : "");
        dot.style.setProperty("--c", colours[piece.item.lane || ""] || "var(--soft)");
        if (closed) { dot.style.animationDelay = closing * 0.06 + "s"; closing += 1; }
        dots.appendChild(dot);
      });
      // Plan units that never reached the line have no lane to colour them.
      for (var never = tally.placed; never < tally.total; never += 1) {
        var unplaced = el("i", "open");
        unplaced.style.setProperty("--c", "var(--soft)");
        dots.appendChild(unplaced);
      }
      nodes.push(dots);
      // What is left, piece by piece: its lane's ring, its ticket, its title and where it stands. A lane is named by the
      // piece it is on, so a count per lane read as a count of that piece.
      var LEFT_SHOWN = 8;
      var groups = el("ul", "left-groups");
      open.slice(0, LEFT_SHOWN).forEach(function (piece) {
        var ring = el("i", "left-ring");
        ring.style.setProperty("--c", colours[piece.item.lane || ""] || "var(--soft)");
        ring.setAttribute("aria-hidden", "true");
        var key = subjectOf(piece.item);
        // The row goes to the line with its stop picked; the title is the button a keyboard reaches, and the ticket
        // keeps its own link.
        var title = el("button", "left-title", titleOf(doc, piece.item));
        title.type = "button";
        title.setAttribute("aria-label", "Show " + titleOf(doc, piece.item) + " on the line");
        var row = add(el("li"), ring, key ? issue(key) : null, title, el("span", "left-state", pieceWords(doc, piece.item)));
        waitLines(doc, key, "span", "left-wait").forEach(function (line) { row.appendChild(line); });
        row.addEventListener("click", function (event) {
          if (event.target && event.target.closest && event.target.closest("a")) { return; }
          var stop = [].filter.call(parts.line.querySelectorAll(".stop-btn"), function (button) { return button.getAttribute("data-subject") === key; })[0];
          if (!stop) { return; }
          stop.click();
          var section = parts.line.closest ? parts.line.closest(".chapter") || parts.line : parts.line;
          if (typeof section.scrollIntoView === "function") { section.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" }); }
          // The stop is pointed out by its ticket's card, as if pointed at, once the page has stopped moving: a scroll
          // hides the card, so it is shown after the last one. It goes with the reader's next scroll.
          var point = function () { if (stop.isConnected) { showCard(stop, key); } };
          if (typeof addEventListener === "function" && "onscrollend" in window) { addEventListener("scrollend", point, { once: true }); }
          setTimeout(point, reduced() ? 50 : 900);
        });
        groups.appendChild(row);
      });
      if (open.length > LEFT_SHOWN) { groups.appendChild(el("li", "left-more", "and " + (open.length - LEFT_SHOWN) + " more")); }
      if (!closedOut) { nodes.push(groups); }
    }
    var finish = list(doc.state.finish).filter(isObject);
    // With a plan, its meters take the list's place and show every line the record has.
    var meters = plan ? planMeters(doc) : null;
    if (meters) {
      nodes.push(el("div", "kicker", "The finish line"), meters);
    } else if (finish.length) {
      var after = el("div", "after");
      var first = true;
      finish.forEach(function (line) {
        var met = line.met === true;
        var current = !met && first;
        if (!met) { first = false; }
        // After a close nothing comes next: a line either was met or wasn't.
        after.appendChild(add(el("div", met ? "met" : current && !closedOut ? "on" : ""), el("span", null, met ? "Met" : closedOut ? "Not met" : current ? "Next" : "Then"), text(line.text)));
      });
      nodes.push(el("div", "kicker", "The finish line"), after);
    }
    inner.replaceChildren.apply(inner, nodes);
    prev.dots = nowOpen;
  }

  /* The plan: is the work still what was agreed, and what proves it? */
  // Proofs are derived from each record the page draws, by the same lookups The line uses; the plan itself comes from the
  // renderer, at load and from each page it renders after. Every unit, row and fold is found through these maps, never through a selector built from plan text.
  var planFolds = Object.create(null);
  var planAsApproved = false;
  var planRows = Object.create(null);
  var FOLD_KEY = (typeof VIEW.themeKey === "string" && VIEW.themeKey ? VIEW.themeKey.replace(/:theme$/, "") : "delivery-views") + ":plan-folds";
  try {
    list(JSON.parse(localStorage.getItem(FOLD_KEY) || "[]")).forEach(function (name) { if (typeof name === "string") { planFolds[name] = true; } });
  } catch (error) { /* folds start closed and are remembered until the page reloads */ }
  function rememberFolds() {
    try { localStorage.setItem(FOLD_KEY, JSON.stringify(Object.keys(planFolds).filter(function (name) { return planFolds[name]; }))); }
    catch (error) { /* remembered until the page reloads */ }
  }
  function own(holder, key) { return isObject(holder) && Object.prototype.hasOwnProperty.call(holder, key) ? holder[key] : undefined; }
  function planUnits(units) { return list(units).filter(isObject); }
  function liveUnits() { return planUnits(plan.units).filter(function (unit) { return unit.removed !== true; }); }
  function kindsOf() { return list(plan.proofKinds).filter(isObject); }
  function kindLabel(id) { var kind = kindsOf().filter(function (entry) { return entry.id === id; })[0]; return kind ? text(kind.label) || text(id) : text(id); }
  function kindSource(id) { var kind = kindsOf().filter(function (entry) { return entry.id === id; })[0]; return kind ? kind.source : null; }
  function unitName(unit) { return text(unit.label) || text(unit.subject); }
  function queueItem(doc, subject) {
    return list(doc.state.queue).filter(function (item) { return isObject(item) && subjectOf(item) === subject; })[0] || null;
  }
  // A lens verdict keyed by the lens's id, or by the name the configuration gives it.
  function verdictOf(verdicts, lens) {
    if (!isObject(verdicts)) { return undefined; }
    var named = isObject(VIEW.lenses) && isObject(own(VIEW.lenses, lens)) ? text(own(VIEW.lenses, lens).name) : "";
    var found = own(verdicts, lens);
    return found !== undefined ? found : named ? own(verdicts, named) : undefined;
  }
  function approves(verdicts) {
    var required = list(plan.requiredLenses).map(text);
    if (!required.length) {
      var words = isObject(verdicts) ? Object.keys(verdicts).map(function (key) { return verdicts[key]; }) : [];
      return words.length > 0 && words.every(function (word) { return word === "approve"; });
    }
    return required.every(function (lens) { return verdictOf(verdicts, lens) === "approve"; });
  }
  function laterThan(at, than) { var a = seconds(at), b = seconds(than); return a !== null && (b === null || a > b); }

  /**
   * The unit's review, from the first source that applies: its run's latest round, an available review observation
   * of the queue head, then the journal's rounds. A round that names no candidate counts only after the hand-off
   * before the current one. { state: done | stale | open | none, at } with `at` the approving round's time.
   */
  function reviewProof(doc, subject, item) {
    var handed = journalFor(doc, subject, { "candidate.handed": true });
    var boundary = handed.length > 1 ? handed[handed.length - 2] : null;
    var journaled = journalFor(doc, subject, { "review.round": true }).filter(function (entry) { return roundOf(entry) !== null; });
    var overlay = Object.create(null);
    journaled.forEach(function (entry) { var round = roundOf(entry); if (isObject(round.verdicts)) { overlay[round.round] = round.verdicts; } });
    function judged(approved, current, at) {
      if (current) { return approved ? { state: "done", at: at } : { state: "open", at: null }; }
      return approved ? { state: "stale", at: null } : { state: "open", at: null };
    }
    if (item && typeof item.runId === "string" && item.runId) {
      var summary = own(runs, item.runId);
      var rounds = isObject(summary) && summary.unavailable !== true ? list(summary.roundDetail).filter(isObject) : [];
      if (rounds.length) {
        var last = rounds[rounds.length - 1];
        var verdicts = isObject(last.verdicts) && Object.keys(last.verdicts).length ? last.verdicts : overlay[last.round];
        var approved = verdicts ? approves(verdicts) : last.outcome === "approve" && !list(plan.requiredLenses).length;
        if (typeof last.candidateRef === "string" && last.candidateRef) { return judged(approved, last.candidateRef === item.head, last.at); }
        return judged(approved, !boundary || laterThan(last.at, boundary.at), last.at);
      }
    }
    var native = own(nativeReviews, subject);
    var observed = isObject(doc.reviewEvidence) ? own(doc.reviewEvidence, subject) : undefined;
    if (item && isObject(native) && native.unavailable !== true
        && (isObject(native.carried) ? native.carried.to : native.candidateRef) === item.head
        && (!isObject(doc.reviewEvidence) || (isObject(observed) && observed.unavailable !== true && observed.digest === native.digest))) {
      var detail = list(native.roundDetail).filter(isObject);
      var final = detail[detail.length - 1];
      var captured = journalFor(doc, subject, KEPT).pop();
      if (final) { return judged(final.outcome === "approve" && approves(final.verdicts), true, captured ? captured.at : null); }
    }
    if (journaled.length) {
      var latest = journaled[journaled.length - 1];
      return judged(approves(roundOf(latest).verdicts), !boundary || (latest.id || 0) > (boundary.id || 0), text(roundOf(latest).finishedAt) || latest.at);
    }
    return { state: "none", at: null };
  }

  /** One owed proof of a unit: { state: done | waived | stale | open | todo | none, words, reason? }. */
  function settledEntry(doc, subject) {
    return list(doc.state.planned).filter(function (entry) { return isObject(entry) && subjectOf(entry) === subject && entry.stage === "settled"; })[0] || null;
  }
  function proofOf(doc, unit, kind) {
    var subject = text(unit.subject);
    // Closed with no change needed: every proof it owes is the lead's accepted evidence, as the close counts it.
    if (settledEntry(doc, subject)) { return { state: "done", words: "closed, no change needed" }; }
    var item = queueItem(doc, subject);
    if (kindSource(kind) === "producer") {
      var entry = own(own(plan.evidence, subject), kind);
      if (!isObject(entry) || entry.state === "todo") { return { state: "todo", words: "to do" }; }
      if (item && entry.head !== item.head) { return { state: "stale", words: "for another head" }; }
      return entry.state === "waived" ? { state: "waived", words: "waived", reason: text(entry.reason) } : { state: "done", words: "done" };
    }
    if (kind === "handed") {
      if (!item) { return { state: "open", words: "not handed off yet" }; }
      if (item.stage === "stopped") { return { state: "open", words: "stopped" }; }
      // A merge recorded outside the queue can leave the flag set; what merged is past waiting.
      if (item.pending === true && item.stage !== "merged") { return { state: "open", words: "waiting for the lead to accept" }; }
      return { state: "done", words: "done" };
    }
    if (kind === "merged") {
      return item && item.stage === "merged" ? { state: "done", words: "done" } : { state: "open", words: "not merged yet" };
    }
    var review = reviewProof(doc, subject, item);
    var words = { done: "approved", stale: "approved for another head", open: "not approved yet", none: "not recorded" };
    return { state: review.state, words: words[review.state], at: review.at };
  }

  /** Every unit's proofs, and whether it is proven: every owed proof done or waived, none stale. */
  function planProofs(doc, units) {
    var proofs = Object.create(null);
    units.forEach(function (unit) {
      var subject = text(unit.subject);
      var owed = list(unit.proofs).map(text);
      var states = Object.create(null);
      kindsOf().forEach(function (kind) { states[kind.id] = proofOf(doc, unit, kind.id); });
      var proven = owed.every(function (kind) { return states[kind].state === "done" || states[kind].state === "waived"; });
      var waived = owed.some(function (kind) { return states[kind].state === "waived"; });
      var next = owed.filter(function (kind) { return states[kind].state !== "done" && states[kind].state !== "waived"; })[0] || null;
      proofs[subject] = { unit: unit, owed: owed, states: states, proven: proven, waived: waived, next: next,
        handed: Boolean(queueItem(doc, subject)), settled: Boolean(settledEntry(doc, subject)),
        review: states.review || proofOf(doc, unit, "review") };
    });
    return proofs;
  }

  /** N of M criteria proven, W of them by waiver, over the units not removed. */
  function planCounts(doc) {
    var units = liveUnits();
    var proofs = planProofs(doc, units);
    var counts = { proven: 0, total: 0, waived: 0, proofs: proofs, units: units };
    units.forEach(function (unit) {
      var size = list(unit.criteria).length;
      var proof = proofs[text(unit.subject)];
      counts.total += size;
      if (proof.proven) { counts.proven += size; if (proof.waived) { counts.waived += size; } }
    });
    return counts;
  }
  function baseBroken(doc) {
    var events = list(doc.journal).filter(function (entry) { return isObject(entry) && (entry.kind === "base.broken" || entry.kind === "base.repaired"); });
    return events.length > 0 && events[events.length - 1].kind === "base.broken";
  }
  function planTitle(doc, counts) {
    var waiver = counts.waived ? ", " + counts.waived + " by waiver" : "";
    if (counts.total && counts.proven === counts.total && !baseBroken(doc)) { return "Every criterion is proven" + waiver + "."; }
    return counts.proven + " of " + counts.total + " criteria proven" + waiver + ".";
  }
  function timesChanged(count) { return "plan changed " + count + (count === 1 ? " time" : " times"); }

  /** How much of the plan is proven, opening the plan chapter. */
  function planNow(doc) {
    if (!plan) { return null; }
    var counts = planCounts(doc);
    var link = figures(el("a", "plan-now-link"), "plan-now", counts.proven + " of " + counts.total + " criteria proven");
    link.setAttribute("href", "#plan");
    return add(el("span", "plan-now-line"), link, " · " + timesChanged(list(plan.changes).length));
  }

  /** The criteria a finish line covers, or null when it covers proofs only, over every unit. */
  function coveredCriteria(units, covers) {
    var criteria = [];
    units.forEach(function (unit) { list(unit.criteria).filter(isObject).forEach(function (criterion) { criteria.push({ id: criterion.id, unit: unit }); }); });
    if (!isObject(covers) || covers.criteria === "all") { return criteria; }
    var named = list(covers.criteria);
    if (!named.length) { return null; }
    return criteria.filter(function (criterion) { return named.indexOf(criterion.id) >= 0; });
  }
  /**
   * A finish line's meter: the share whose covered proofs are done, then the share done or waived, named by what it
   * counts. "Met" is the record's word; a line covering no criteria and no proofs has nothing to count, so no bar.
   */
  function meter(counts, line, recordLine) {
    var met = Boolean(recordLine) && recordLine.met === true;
    var label = line ? text(line.label) || text(line.id) : text(recordLine.text) || text(recordLine.id);
    var done = 0, either = 0, total = 0, noun = "criteria", measured = "Proven";
    var kinds = [];
    var moved = line ? finishMoved(line) : null;
    if (line) {
      kinds = list(isObject(line.covers) ? line.covers.proofs : []).map(text);
      if (kinds.length) { measured = kinds.map(kindLabel).join(", "); }
      var settled = function (unit, waivedToo) {
        var proof = counts.proofs[text(unit.subject)];
        if (!kinds.length) { return proof.proven && (waivedToo || !proof.waived); }
        // A kind the unit does not owe is settled for it, so the meter and the title agree.
        return kinds.every(function (kind) {
          if (proof.owed.indexOf(kind) < 0) { return true; }
          var state = proof.states[kind].state;
          return state === "done" || (waivedToo && state === "waived");
        });
      };
      var covered = coveredCriteria(counts.units, line.covers);
      if (covered === null && !kinds.length) {
        // Nothing to count: the lead marks it, so its bar is all or nothing and its line says whose word it is.
        var marked = el("div", "pm-bar");
        var whole = el("i", "pm-done");
        whole.style.width = met ? "100%" : "0%";
        marked.appendChild(whole);
        return add(el("div", "plan-meter" + (met ? " met" : "")), add(el("div", "pm-head"), el("span", "pm-label", label), el("span", "pm-state", met ? "Met" : "Not yet")),
          marked, el("div", "pm-count", met ? "Marked met by the lead" : "The lead marks this one"), moved);
      }
      var rows = covered === null ? counts.units : covered.map(function (criterion) { return criterion.unit; });
      if (covered === null) { noun = "units"; }
      total = rows.length;
      done = rows.filter(function (unit) { return settled(unit, false); }).length;
      either = rows.filter(function (unit) { return settled(unit, true); }).length;
    }
    // The record can say met while proofs it covers are still open: say both rather than let one hide the other.
    var open = met && line && either < total;
    var counted = !met && line && total > 0 && either === total;
    var box = el("div", "plan-meter" + (open ? " open" : met ? " met" : ""));
    var head = add(el("div", "pm-head"), el("span", "pm-label", label), el("span", "pm-state", open ? "Met, proofs open" : met ? "Met" : counted ? "Ready to mark" : "Not yet"));
    var bar = el("div", "pm-bar");
    var full = el("i", "pm-done");
    var waived = el("i", "pm-waived");
    full.style.width = (total ? done / total * 100 : 0) + "%";
    waived.style.width = (total ? (either - done) / total * 100 : 0) + "%";
    add(bar, full, waived);
    // The count first, then what it counts: "10 of 21 criteria · review, merged".
    var words = line ? done + " of " + total + " " + noun + (kinds.length ? " · " + measured.toLowerCase() : " proven") +
      (either > done ? ", " + (either - done) + " more by waiver" : "") : "Not in the plan";
    return add(box, head, bar, figures(el("div", "pm-count"), "meter:" + (line ? text(line.id) : text(recordLine.id)), words), moved);
  }
  /** One meter per finish line: the plan's, in order, then any the record has that the plan does not. */
  function planMeters(doc) {
    if (!plan) { return null; }
    var counts = planCounts(doc);
    var record = list(doc.state.finish).filter(isObject);
    var lines = list(plan.finish).filter(isObject);
    var row = el("div", "plan-meters");
    lines.forEach(function (line) { row.appendChild(meter(counts, line, record.filter(function (entry) { return entry.id === line.id; })[0] || null)); });
    record.filter(function (entry) { return !lines.some(function (line) { return line.id === entry.id; }); })
      .forEach(function (entry) { row.appendChild(meter(counts, null, entry)); });
    return row.children.length ? row : null;
  }

  /** The criteria a change touched: the ones it names, or every criterion of the units it added. */
  function touchedCriteria(change) {
    var named = list(change.criteria).map(text);
    if (named.length || change.kind !== "added") { return named; }
    var ids = [];
    planUnits(plan.units).filter(function (unit) { return list(change.subjects).indexOf(unit.subject) >= 0; })
      .forEach(function (unit) { list(unit.criteria).filter(isObject).forEach(function (criterion) { ids.push(criterion.id); }); });
    return ids;
  }
  /** C7's flags, in order: each { title, why }. */
  function planFlags(doc, counts) {
    var flags = [];
    var changes = list(plan.changes).filter(isObject);
    var events = list(doc.journal).filter(function (entry) { return isObject(entry) && (entry.kind === "base.broken" || entry.kind === "base.repaired"); });
    if (baseBroken(doc)) { flags.push({ title: "The base is broken", why: toldLine(doc, events[events.length - 1]) || "No reason recorded.", fix: "Repair the base; nothing lands until it is." }); }
    // Only an approval the lead marked blocking needs the engineer; one that is merely ahead stays in its fold.
    var blocking = list(plan.approvals).filter(function (entry) { return isObject(entry) && entry.blocking === true && entry.state === "waiting"; });
    if (blocking.length) { flags.push({ title: "Approvals are blocking work", why: blocking.map(function (entry) { return text(entry.text); }).join("; "), fix: blocking.length === 1 ? "Give or refuse this approval." : "Give or refuse each approval." }); }
    var known = Object.create(null);
    planUnits(plan.units).forEach(function (unit) { known[text(unit.subject)] = true; });
    var stray = [];
    list(doc.state.queue).concat(list(doc.state.planned)).filter(isObject).forEach(function (item) {
      var subject = subjectOf(item);
      if (subject && !known[subject] && stray.indexOf(subject) < 0) { stray.push(subject); }
    });
    if (stray.length) { flags.push({ title: "Work in the record isn't in the plan", why: stray.join(", "), fix: stray.length === 1 ? "Add it to the plan as a unit." : "Add each to the plan as a unit." }); }
    var planIds = list(plan.finish).filter(isObject).map(function (line) { return text(line.id); });
    var recordIds = list(doc.state.finish).filter(isObject).map(function (line) { return text(line.id); });
    var onlyPlan = planIds.filter(function (id) { return recordIds.indexOf(id) < 0; });
    var onlyRecord = recordIds.filter(function (id) { return planIds.indexOf(id) < 0; });
    if (onlyPlan.length || onlyRecord.length) {
      flags.push({ title: "The plan and the record disagree on finish lines", why: [onlyPlan.length ? "Only in the plan: " + onlyPlan.join(", ") : "",
        onlyRecord.length ? "Only in the record: " + onlyRecord.join(", ") : ""].filter(Boolean).join(". "), fix: "Make the plan's finish lines match the record's." });
    }
    var live = Object.create(null);
    counts.units.forEach(function (unit) { list(unit.criteria).filter(isObject).forEach(function (criterion) { live[criterion.id] = true; }); });
    list(plan.finish).filter(isObject).forEach(function (line) {
      var named = isObject(line.covers) && Array.isArray(line.covers.criteria) ? line.covers.criteria : null;
      if (!named || !named.length) { return; }
      var missed = [];
      changes.filter(function (change) { return (change.kind === "added" || change.kind === "changed" || change.kind === "relaxed") && laterThan(change.at, line.at); })
        .forEach(function (change) {
          touchedCriteria(change).forEach(function (id) { if (live[id] && named.indexOf(id) < 0 && missed.indexOf(id) < 0) { missed.push(id); } });
        });
      if (missed.length) { flags.push({ title: "A finish line no longer covers the plan", why: (text(line.label) || text(line.id)) + " omits " + missed.join(", "), fix: "Add the missing criteria to the line's covers, or have it cover all." }); }
    });
    var touched = [];
    changes.filter(function (change) { return change.kind === "relaxed" || change.kind === "changed" || change.kind === "removed"; }).forEach(function (change) {
      list(change.subjects).map(text).forEach(function (subject) {
        var proof = counts.proofs[subject];
        if (!proof || proof.proven || touched.indexOf(subject) >= 0) { return; }
        if (proof.review.state === "done" && laterThan(proof.review.at, change.at)) { return; }
        touched.push(subject);
      });
    });
    if (touched.length) { flags.push({ title: "A change touches open work", why: touched.join(", "), fix: (touched.length === 1 ? "Have it reviewed again" : "Have each reviewed again") + "; the flag clears once a review approves after the change." }); }
    // Clipping empties a reason that was there; a change that never had one still has none.
    var unexplained = changes.filter(function (change) { return own(change, "why") === undefined || (change.clipped !== true && !text(change.why)); });
    if (unexplained.length) {
      flags.push({ title: unexplained.length === 1 ? "A change has no reason" : unexplained.length + " changes have no reason",
        why: unexplained.map(function (change) { return text(change.title) || text(change.id); }).join(", "), fix: unexplained.length === 1 ? "Give the change a why." : "Give each change a why." });
    }
    var waivedOpen = counts.units.filter(function (unit) { var proof = counts.proofs[text(unit.subject)]; return !proof.proven && list(unit.proofs).some(function (kind) { return proof.states[kind].state === "waived"; }); });
    if (waivedOpen.length) { flags.push({ title: "A waived proof on a unit not yet proven", why: waivedOpen.map(unitName).join(", "), fix: "Finish the unit's other proofs, or check the waiver was meant." }); }
    return flags;
  }
  function flagList(flags, closed) {
    var box = el("div", "plan-needs");
    if (!flags.length) { return closed ? null : add(box, el("p", "plan-calm", "Nothing in the plan needs attention right now.")); }
    var ul = el("ul");
    flags.forEach(function (flag) {
      // What is wrong, what it names, then what settles it. Nobody asked the reader for these: the page found the plan and
      // the record out of step, so the heading says so rather than "Needs you", which is the supervisor's request.
      ul.appendChild(add(el("li"), glyph("stop"), add(el("div"), el("div", "a1", flag.title), flag.why ? el("div", "a2", flag.why) : null,
        flag.fix && !closed ? el("div", "a3", flag.fix) : null)));
    });
    return add(box, el("div", "ah", closed ? "Left open at close" : "The plan needs attention"), ul);
  }

  /**
   * A proof still owed, as a sentence about the piece, in the words its panel on the line uses; a waived one keeps its
   * label and says so.
   */
  function proofLine(proof, kind, closed) {
    var state = proof.states[kind];
    if (state.state === "waived") { return add(el("span", "plan-proof waived"), el("span", "pp-kind", kindLabel(kind)), el("span", "pp-state", state.words)); }
    return el("span", "plan-proof " + state.state + (proofStuck(kind, proof) ? " stuck" : ""), proofSaid(kind, proof, closed));
  }
  /** Where a unit stands in one phrase: proven, or whether it merged and how many proofs are still open. */
  function unitStanding(proof) {
    if (proof.settled) { return "Closed, no change needed"; }
    if (proof.proven) { return proof.waived ? "Proven, with a waiver" : "Proven"; }
    var open = proof.owed.filter(function (kind) { return proof.states[kind].state !== "done" && proof.states[kind].state !== "waived"; });
    return (proof.owed.indexOf("merged") >= 0 && proof.states.merged.state === "done" ? "Merged, " : "") + open.length + (open.length === 1 ? " proof left" : " proofs left");
  }
  /**
   * The next proof a unit owes, as a sentence about the piece: "It still has to merge." Once the delivery has closed
   * nothing more will happen to it, so the sentence says what never did: "It never merged."
   */
  function proofSaid(kind, proof, closed) {
    var state = proof.states[kind], words = state.words;
    if (kind === "merged") { return closed ? "It never merged." : "It still has to merge."; }
    if (kind === "handed") {
      return words === "stopped" ? "Its checks stopped it." : closed ? "It was never handed off."
        : words === "waiting for the lead to accept" ? "It's waiting for the lead to accept it." : "It hasn't been handed off yet.";
    }
    if (kindSource(kind) === "producer") {
      return kindLabel(kind) + (state.state === "stale" ? " was done for an earlier commit." : closed ? " was never done." : " is still to do.");
    }
    return state.state === "stale" ? "Its approval is for an earlier commit." : state.state === "none" ? "No review is recorded for it."
      : closed ? "Its review was never approved." : "Its review isn't approved yet.";
  }
  /** Whether the next proof is held up, not just still ahead: done for another commit, or stopped. */
  function proofStuck(kind, proof) { return proof.states[kind].state === "stale" || proof.states[kind].words === "stopped"; }
  function unitRow(proof, kinds, asApproved) {
    var unit = proof.unit;
    var row = el("div", "plan-unit" + (unit.removed === true ? " removed" : proof && proof.proven ? " proven" : ""));
    var name = add(el("div", "pu-name"), issue(text(unit.subject), unitName(unit)), el("span", "pu-title", text(unit.title)));
    if (unitName(unit) !== text(unit.subject)) { name.appendChild(el("span", "pu-key", text(unit.subject))); }
    if (unit.removed === true) { name.appendChild(el("span", "pu-removed", "Removed")); }
    var criteria = el("ul", "pu-criteria");
    list(unit.criteria).filter(isObject).forEach(function (criterion) { criteria.appendChild(add(el("li"), text(criterion.text), " ", el("span", "pu-id", text(criterion.id)))); });
    var status = el("div", "pu-status");
    if (unit.removed !== true) { status.appendChild(el("div", "pu-standing", unitStanding(proof))); }
    if (unit.removed !== true) {
      var owed = kinds.filter(function (kind) { return list(unit.proofs).indexOf(kind.id) >= 0; });
      function finished(kind) { return proof.states[kind.id].state === "done"; }
      var cells = el("div", "pu-proofs");
      // What is still open comes first, a sentence each; what is finished reads as one quiet line after it.
      var over = Boolean(shown && closedEntry(shown));
      owed.filter(function (kind) { return !finished(kind); }).forEach(function (kind) { cells.appendChild(proofLine(proof, kind.id, over)); });
      var finishedKinds = owed.filter(finished);
      if (finishedKinds.length && !proof.proven) {
        cells.appendChild(el("span", "plan-proof done", "Done: " + finishedKinds.map(function (kind) { return kindLabel(kind.id).toLowerCase(); }).join(", ")));
      } else if (finishedKinds.length) {
        cells.appendChild(el("span", "plan-proof done", finishedKinds.map(function (kind) { return kindLabel(kind.id); }).join(" · ")));
      }
      if (owed.some(function (kind) { return !finished(kind) && proofStuck(kind.id, proof); })) { row.classList.add("stuck"); }
      status.appendChild(cells);
    }
    return add(row, name, criteria, status);
  }
  /** A fold the reader opens; open folds are remembered for this page. */
  function fold(name, label, count, body) {
    var box = el("details", "plan-fold");
    box.setAttribute("data-fold", name);
    if (planFolds[name]) { box.open = true; }
    var summary = el("summary", null, label);
    if (count !== null) { summary.appendChild(el("span", "pf-count", String(count))); }
    add(box, summary, body);
    return runFold(box, { onChange: function (open) { planFolds[name] = open; rememberFolds(); } });
  }
  /** Before and after as one text, with what was dropped struck and what was added marked, word by word. */
  function redline(before, after) {
    // Marks are for a rewording, where they show which words moved. Text with nothing before it is simply new, and text
    // with nothing after it simply gone: marking every word would say only what the change's heading already does.
    if (!text(before).trim()) { return el("div", "redline", text(after)); }
    if (!text(after).trim()) { return add(el("div", "redline"), el("del", null, text(before))); }
    var a = text(before).split(/(\s+)/), b = text(after).split(/(\s+)/);
    var table2 = [];
    for (var i = a.length; i >= 0; i -= 1) {
      table2[i] = [];
      for (var j = b.length; j >= 0; j -= 1) {
        table2[i][j] = i === a.length || j === b.length ? 0 : a[i] === b[j] ? table2[i + 1][j + 1] + 1 : Math.max(table2[i + 1][j], table2[i][j + 1]);
      }
    }
    var box = el("div", "redline");
    var x = 0, y = 0;
    while (x < a.length || y < b.length) {
      if (x < a.length && y < b.length && a[x] === b[y]) { box.appendChild(document.createTextNode(a[x])); x += 1; y += 1; }
      else if (x < a.length && (y === b.length || table2[x + 1][y] >= table2[x][y + 1])) { box.appendChild(el("del", null, a[x])); x += 1; }
      else { box.appendChild(el("ins", null, b[y])); y += 1; }
    }
    return box;
  }
  function coversText(covers) {
    if (!isObject(covers)) { return text(covers); }
    var criteria = covers.criteria === "all" ? "every criterion" : list(covers.criteria).length ? list(covers.criteria).join(", ") : "no criteria";
    return criteria + "; proofs: " + (list(covers.proofs).length ? list(covers.proofs).map(kindLabel).join(", ") : "none");
  }
  var CHANGE_WORDS = table({ added: "Added", removed: "Removed", relaxed: "Relaxed", changed: "Changed", finish: "Finish line" });
  /**
   * The scope over time: units in the plan from approval through each change. The clock runs from approval to the last
   * change, so the changes get the width; the quiet stretch since then is a short dotted tail to now. Changes too close to
   * tell apart share one mark, drawn as the weightiest of them. The counts sit at each end and the dates beneath.
   */
  var SCOPE_WEIGHT = { added: 1, finish: 2, relaxed: 2, changed: 2, removed: 3 };
  function scopeChart(changes) {
    var svgNs = "http://www.w3.org/2000/svg";
    var start = seconds(isObject(plan.approved) ? plan.approved.at : null);
    var times = changes.map(function (change) { return seconds(change.at); }).filter(function (t) { return t !== null; });
    var last = times.length ? Math.max.apply(null, times) : null;
    var begin = start === null ? (times.length ? Math.min.apply(null, times) : Date.now() / 1000) : start;
    var size = planUnits(isObject(plan.approved) && isObject(plan.approved.snapshot) ? plan.approved.snapshot.units : []).length;
    var first = size;
    var points = [[begin, size]];
    changes.slice().sort(function (p, q) { return (seconds(p.at) || 0) - (seconds(q.at) || 0); }).forEach(function (change) {
      // A change naming criteria added or dropped those, and its story stays in scope.
      if (change.kind === "added" && !list(change.criteria).length) { size += list(change.subjects).length; }
      if (change.kind === "removed" && !list(change.criteria).length) { size -= list(change.subjects).length; }
      points.push([seconds(change.at) || begin, size, change.kind]);
    });
    var end = last !== null && last > begin ? last : begin + 1;
    var W = 600, LEFT = 8, TAIL = 64, RIGHT = W - 8 - TAIL, TOP = 22, BOTTOM = 64;
    var lowest = Math.min.apply(null, points.map(function (point) { return point[1]; }));
    var highest = Math.max.apply(null, points.map(function (point) { return point[1]; }).concat([lowest + 1]));
    var x = function (t) { return LEFT + Math.max(0, Math.min(1, (t - begin) / (end - begin))) * (RIGHT - LEFT); };
    var y = function (n) { return BOTTOM - (n - lowest) / (highest - lowest) * (BOTTOM - TOP); };
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "plan-scope");
    svg.setAttribute("viewBox", "0 0 " + W + " 92");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Units in scope: " + first + " at approval, " + size + " now, over " + changes.length + " changes");
    var path = "M" + x(points[0][0]).toFixed(1) + " " + y(points[0][1]).toFixed(1);
    points.slice(1).forEach(function (point) { path += " H" + x(point[0]).toFixed(1) + " V" + y(point[1]).toFixed(1); });
    path += " H" + RIGHT;
    shape(svg, "path", { class: "ps-line", d: path });
    shape(svg, "line", { class: "ps-tail", x1: RIGHT, x2: W - 8, y1: y(size).toFixed(1), y2: y(size).toFixed(1) });
    // Marks a few pixels apart merge: the group keeps the weightiest kind and sits at its last step.
    var marks = [];
    points.slice(1).forEach(function (point) {
      var at = x(point[0]), held = marks[marks.length - 1];
      if (held && at - held.x < 6) {
        held.y = y(point[1]); held.count += 1;
        if ((SCOPE_WEIGHT[point[2]] || 0) > (SCOPE_WEIGHT[held.kind] || 0)) { held.kind = point[2]; }
      } else {
        marks.push({ x: at, y: y(point[1]), kind: point[2], count: 1 });
      }
    });
    marks.forEach(function (mark) { shape(svg, "circle", { class: "ps-dot " + mark.kind, cx: mark.x.toFixed(1), cy: mark.y.toFixed(1), r: mark.count > 1 ? 3.5 : 2.5 }); });
    // Dates beneath: approval at the left, the last change where the tail begins. Both on one day, they give the hour.
    var sameDay = last !== null && new Date(begin * 1000).toDateString() === new Date(last * 1000).toDateString();
    var when = function (t) {
      var date = new Date(t * 1000);
      return sameDay ? clockParts(date.toISOString()).time : MONTHS[date.getMonth()] + " " + date.getDate();
    };
    // The counts: where scope began, above its start, and where it stands, above now.
    // With no approval there is no count before the changes, so the label starts from the first change's.
    var opening = start === null && points.length > 1 ? points[1][1] : first;
    shape(svg, "text", { class: "ps-count", x: LEFT, y: y(opening) - 8 }, opening + (start === null ? " after the first change" : " at approval"));
    shape(svg, "text", { class: "ps-count now", x: W - 8, y: y(size) - 8, "text-anchor": "end" }, size + " now");
    shape(svg, "text", { class: "ps-tick", x: LEFT, y: 88 }, when(begin));
    if (last !== null && RIGHT - LEFT > 120) { shape(svg, "text", { class: "ps-tick", x: RIGHT, y: 88, "text-anchor": "end" }, when(last)); }
    shape(svg, "text", { class: "ps-tick", x: W - 8, y: 88, "text-anchor": "end" }, "now");
    return svg;
  }
  function changeRef(ref) {
    var target = changeTarget(ref);
    if (target) { return changeLink(ref); }
    return issueHref(text(ref)) ? issue(text(ref)) : el("span", "pc-ref", text(ref));
  }
  /** What was approved: its units and its finish lines, as the snapshot froze them. */
  function approvedPart(key) { return list(isObject(plan.approved) && isObject(plan.approved.snapshot) ? plan.approved.snapshot[key] : []).filter(isObject); }
  function spokenList(parts) { return parts.length < 2 ? parts.join("") : parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1]; }
  /**
   * The scope since approval in one sentence, counted from the recorded changes alone: stories added and removed, then
   * criteria added, removed and changed. A change naming criteria moved those criteria, not its story.
   */
  function scopeSentence(changes) {
    var approved = approvedPart("units").length;
    var tally = { storyAdded: 0, storyRemoved: 0, added: 0, removed: 0, changed: 0 };
    changes.forEach(function (change) {
      var named = list(change.criteria).length, subjects = list(change.subjects).length;
      if (change.kind === "added") { if (named) { tally.added += named; } else { tally.storyAdded += subjects; } }
      if (change.kind === "removed") { if (named) { tally.removed += named; } else { tally.storyRemoved += subjects; } }
      if (change.kind === "changed" || change.kind === "relaxed") { tally.changed += named; }
    });
    var stories = function (n) { return n + (n === 1 ? " story" : " stories"); };
    var moved = [tally.storyAdded ? tally.storyAdded + " added" : "", tally.storyRemoved ? tally.storyRemoved + " removed" : ""].filter(Boolean);
    var criteria = [[tally.added, "added"], [tally.removed, "removed"], [tally.changed, "changed"]].filter(function (pair) { return pair[0]; })
      .map(function (pair, i) { return pair[0] + (i ? "" : pair[0] === 1 ? " criterion" : " criteria") + " " + pair[1]; });
    if (!moved.length && !criteria.length) { return "The scope is as approved: " + stories(approved) + "."; }
    var now = approved + tally.storyAdded - tally.storyRemoved;
    return "Approved with " + stories(approved) + (moved.length ? ", now " + now + ": " + spokenList(moved) : ", still " + now) +
      (criteria.length ? "; " + spokenList(criteria) : "") + ".";
  }
  /** Whether covers `a` holds everything covers `b` does: its criteria, or every criterion, and its proofs. */
  function coversHold(a, b) {
    var proofs = list(a.proofs).map(text);
    var criteria = a.criteria === "all" || (b.criteria !== "all" && list(b.criteria).every(function (id) { return list(a.criteria).indexOf(id) >= 0; }));
    return criteria && list(b.proofs).every(function (kind) { return proofs.indexOf(text(kind)) >= 0; });
  }
  function coversWords(covers) {
    var criteria = covers.criteria === "all" ? "every criterion" : list(covers.criteria).length ? list(covers.criteria).map(text).join(", ") : "no criteria";
    var proofs = list(covers.proofs).map(function (kind) { return kindLabel(text(kind)).toLowerCase(); });
    return criteria + (proofs.length ? " (" + proofs.join(", ") + ")" : "");
  }
  /** A finish line that moved since approval: added since, or its covers raised, lowered or changed, and when. */
  function finishMoved(line) {
    var was = approvedPart("finish").filter(function (entry) { return entry.id === line.id; })[0];
    // A line edited by hand carries the approval's time, which isn't when it moved: then it says no time.
    var parts = laterThan(line.at, isObject(plan.approved) ? plan.approved.at : null) ? clockParts(line.at) : null;
    var when = !parts ? "" : parts.day ? " on " + parts.day + ", " + parts.time : " at " + parts.time;
    if (!was) { return el("div", "pm-count pm-moved", "Added since approval" + when + "."); }
    var before = isObject(was.covers) ? was.covers : {}, after = isObject(line.covers) ? line.covers : {};
    var raised = coversHold(after, before), lowered = coversHold(before, after);
    if (raised && lowered) { return null; }
    return el("div", "pm-count pm-moved", (raised ? "Raised" : lowered ? "Lowered" : "Changed") + when + ": from " + coversWords(before) +
      " to " + coversWords(after) + ".");
  }
  function changeRow(change) {
    // A removal naming criteria dropped those from its story, which stays: its wording is struck through below.
    var dropped = change.kind === "removed" && list(change.criteria).length > 0;
    var row = el("li", "plan-change " + text(change.kind) + (dropped ? " criterion" : ""));
    var head = add(el("div", "pc-head"), el("span", "pc-kind", dropped ? "Removed criterion" : CHANGE_WORDS[change.kind] || text(change.kind)),
      el("span", "pc-subjects", list(change.subjects).map(text).join(", ") + (dropped ? " · " + list(change.criteria).map(text).join(", ") : "")),
      el("time", null, clock(change.at)));
    head.lastChild.setAttribute("datetime", text(change.at));
    // What was approved was reviewed with the plan; a change made since was not.
    if (laterThan(change.at, isObject(plan.approved) ? plan.approved.at : null)) { head.appendChild(el("span", "pc-unreviewed", "Not reviewed with the plan")); }
    add(row, head);
    if (text(change.title)) { row.appendChild(el("div", "pc-title", text(change.title))); }
    var before = own(change, "before"), after = own(change, "after");
    if (isObject(before) || isObject(after)) { row.appendChild(redline(coversText(before), coversText(after))); }
    else if (before !== undefined || after !== undefined) { row.appendChild(redline(before, after)); }
    var meta = [change.by === "unrecorded" ? "Not recorded by a command" : "By " + text(change.by)];
    if (text(change.why)) { meta.push(text(change.why)); } else if (own(change, "why") === undefined || change.clipped !== true) { meta.push("No reason given"); }
    var foot = add(el("div", "pc-why"), meta.join(" · "));
    if (text(change.source)) { add(foot, " · ", text(change.source)); }
    if (text(change.ref)) { add(foot, " · ", changeRef(change.ref)); }
    if (change.clipped === true) { add(foot, el("span", "pc-clipped", " · text clipped")); }
    return add(row, foot);
  }

  /** The plan's review as the run summary draws a review: its entries, how it ended, and that it has no bound. */
  function planReview() { return isObject(plan.planReview) && list(plan.planReview.rounds).filter(isObject).length ? plan.planReview : null; }
  function reviewRounds(review) {
    var rounds = list(review.rounds).filter(isObject);
    var last = rounds[rounds.length - 1].round;
    return typeof last === "number" ? last : rounds.length;
  }
  var REVIEW_ENDINGS = table({ aligned: "every lens aligned", ended: "ended without alignment", open: "still open" });
  /** "Approved Oct 30 after 3 review rounds · every lens aligned": open by default, beside the approval. */
  function approvalLine(review) {
    var at = seconds(isObject(plan.approved) ? plan.approved.at : null);
    var date = at === null ? null : new Date(at * 1000);
    var day = date ? MONTHS[date.getMonth()] + " " + date.getDate() + (date.getFullYear() === new Date(Date.now()).getFullYear() ? "" : ", " + date.getFullYear()) : "at an unknown time";
    var count = reviewRounds(review);
    return el("p", "plan-approval", "Approved " + day + " after " + count + (count === 1 ? " review round" : " review rounds") +
      (REVIEW_ENDINGS[review.state] ? " · " + REVIEW_ENDINGS[review.state] : ""));
  }
  function reviewFold(review) {
    var count = reviewRounds(review);
    var summary = { observational: true, observationKey: "plan-review", open: false, roundDetail: list(review.rounds).filter(isObject),
      planReview: { state: text(review.state), endedReason: text(review.endedReason) } };
    var box = el("div", "plan-review");
    box.appendChild(runSummaryNode(summary, { commitHref: commitHref, issueHref: issueHref, lenses: VIEW.lenses }));
    return fold("review", "Plan review (" + count + (count === 1 ? " round)" : " rounds)"), null, box);
  }

  // The plan a live page holds follows the record. The server renders the page again when the record moves, so the plan
  // comes from that page's block: what the renderer's validator accepted, or { unreadable }. It renders after it pushes,
  // so an answer holding an older plan means not yet; asking stops being quiet after PLAN_PATIENCE of those. Once it has
  // said the plan is out of date it goes on saying so until the plan is current.
  var PLAN_FIRST = 1000, PLAN_AGAIN = 2000, PLAN_BEHIND = 30000, PLAN_PATIENCE = 15;
  var planNamed = null, planRested = null, planTries = 0, planBehind = false, planAsked = null, planAsking = false;
  function servedLive() {
    return isObject(globalThis.deliveryStateStore) && typeof fetch === "function" && typeof location === "object" && /^https?:$/.test(location.protocol);
  }
  /** The `updated` value of the latest plan update in a record, as its plan.updated entry names it, or null. */
  function planNamedBy(doc) {
    var updates = list(doc.journal).filter(function (entry) {
      return isObject(entry) && entry.kind === "plan.updated" && isObject(entry.data) && typeof entry.data.updated === "string";
    });
    var newest = updates[updates.length - 1];
    return newest ? newest.data.updated : null;
  }
  // Whether an answer holds the plan named, or one the record has yet to name: the file is written before its entry.
  function planMeets(held, named) { return isObject(held) && typeof held.updated === "string" && (held.updated === named || laterThan(held.updated, named)); }
  function followPlan(named) {
    if (named !== planRested) { planRested = null; }
    if (named === null || named === planRested || (isObject(plan) && plan.updated === named)) {
      clearTimeout(planAsked);
      planAsked = null;
      planNamed = null;
      planBehind = false;
      return;
    }
    if (named !== planNamed) {
      clearTimeout(planAsked);
      planAsked = null;
      planNamed = named;
      planTries = 0;
      planBehind = false;
    }
    if (planAsked !== null || planAsking) { return; }
    planAsked = setTimeout(askPlan, planBehind ? PLAN_BEHIND : planTries ? PLAN_AGAIN : PLAN_FIRST);
  }
  function askPlan() {
    planAsked = null;
    planAsking = true;
    fetch(location.href, { cache: "no-store" }).then(function (answer) {
      if (!answer.ok) { throw new Error("no page"); }
      return answer.text();
    }).then(function (page) {
      return JSON.parse(new DOMParser().parseFromString(page, "text/html").getElementById("delivery-plan").textContent);
    }).then(answeredPlan).catch(function () { answeredPlan(null); });
  }
  function answeredPlan(block) {
    planAsking = false;
    // The record stopped naming another plan while the page asked, so there is nothing to take.
    if (planNamed === null) { return; }
    if (isObject(block) && planMeets(block.plan, planNamed)) {
      if (!isObject(plan) || plan.updated !== block.plan.updated) { takePlan(block); return; }
      // The same later plan may be a render still to come, of the earlier plan the record names, or the file ahead of its
      // entry. It is the plan a reload would show once the server has gone on holding it, or has just come back with it.
      planTries += 1;
      if (planBehind || planTries >= PLAN_PATIENCE) {
        planRested = planNamed;
        safely(lastGood || shown);
      } else {
        followPlan(planNamed);
      }
      return;
    }
    var older = isObject(block) && isObject(block.plan) && typeof block.plan.updated === "string" && seconds(block.plan.updated) !== null;
    planTries += 1;
    var behind = planBehind || !older || planTries >= PLAN_PATIENCE;
    if (behind !== planBehind) { planBehind = behind; safely(lastGood || shown); }
    // Only a change of note drew, so ask again from here too; a second ask is refused while one is waiting.
    followPlan(planNamed);
  }
  // The new plan is only kept if the page can draw it: the plan in use goes back otherwise.
  function takePlan(block) {
    var was = { PLAN: PLAN, plan: plan, named: planNamed };
    PLAN = block;
    plan = block.plan;
    planBehind = false;
    var place = planPlace();
    try {
      draw(lastGood || shown);
    } catch (error) {
      PLAN = was.PLAN;
      plan = was.plan;
      planNamed = was.named;
      planBehind = true;
      // The failed draw may have asked at the quick pace; this answer says to ask at the slow one.
      clearTimeout(planAsked);
      planAsked = null;
      safely(lastGood || shown);
    }
    keepPlace(place);
  }
  // Where the reader is. Past the chapter, a plan of another height moves what they are reading, along with anything the
  // new plan changed above it, so the window moves with the chapter's end; anywhere else it stays.
  function planPlace() {
    if (typeof scrollY !== "number") { return null; }
    var bottom = parts.plan.getBoundingClientRect().bottom;
    return { y: scrollY, past: bottom <= 0, end: bottom + scrollY };
  }
  function keepPlace(place) {
    if (!place) { return; }
    var y = place.y + (place.past ? parts.plan.getBoundingClientRect().bottom + scrollY - place.end : 0);
    // The page scrolls smoothly for its links; this is no movement the reader asked for.
    if (scrollY !== y && typeof scrollTo === "function") { scrollTo({ top: y, behavior: "instant" }); }
  }

  function drawPlan(doc) {
    var inner = parts.plan;
    var live = servedLive();
    var named = planNamedBy(doc);
    if (live) { followPlan(named); }
    planRows = Object.create(null);
    var nodes = [el("div", "kicker", "The plan")];
    if (!plan) {
      var reason = PLAN && text(PLAN.unreadable) ? PLAN.unreadable : "";
      nodes.push(flagList([PLAN && typeof PLAN.absent === "string"
        ? { title: "The plan chapter is missing", why: PLAN.absent + " is the plan on file, and nothing has written plan.json for this page.", fix: "Write plan.json for this delivery." }
        : { title: "The plan couldn't be read", why: reason, fix: "Correct plan.json so the renderer accepts it." }], false));
      inner.replaceChildren.apply(inner, nodes);
      return;
    }
    var counts = planCounts(doc);
    // The title is a row of parts; its words are one of them, so a digit that flips stays inside the words.
    var title = add(el("h2", "title plan-title"), figures(el("span"), "plan-title", planTitle(doc, counts)));
    // A record says when the producer rewrote the plan. A live page fetches it and speaks only if that fails; a static
    // page holds the plan it was rendered with.
    var behind = null;
    if (live ? planBehind : named && named !== plan.updated) {
      // Said under the headline, not in it: what is wrong, and the one thing the reader can do about it.
      var again = el("button", null, "Reload the page");
      again.type = "button";
      again.addEventListener("click", function () { if (typeof location === "object" && typeof location.reload === "function") { location.reload(); } });
      behind = add(el("p", "plan-reload"), el("span", null, live ? "The plan has changed, and this page couldn't load the new one." : "The plan has changed since this page was made."), again);
      behind.setAttribute("role", "status");
    }
    var review = planReview();
    nodes.push(title);
    if (behind) { nodes.push(behind); }
    nodes.push(el("p", "say plan-outcome", text(isObject(plan.outcome) ? plan.outcome.summary : "")));
    if (review) { nodes.push(approvalLine(review)); }
    var closeOut = isObject(plan.closeOut) && plan.closeOut.fullHealth === "skipped" ? plan.closeOut : null;
    if (closeOut) {
      nodes.push(el("p", "say plan-close-out", "The close-out's fullHealth run was skipped under the " + text(closeOut.posture)
        + " posture declared on " + text(closeOut.source) + ": " + text(closeOut.reason)));
    }
    var flagged = flagList(planFlags(doc, counts), Boolean(closedEntry(doc)));
    if (flagged) { nodes.push(flagged); }
    var open = counts.units.filter(function (unit) { return !counts.proofs[text(unit.subject)].proven; });
    if (open.length) {
      var ul = el("ul", "plan-open");
      open.forEach(function (unit) {
        var proof = counts.proofs[text(unit.subject)];
        // The same words as the piece's panel on the line: how many proofs are left, then the next one as a sentence.
        var over = Boolean(closedEntry(doc));
        var said = proof.next ? proofSaid(proof.next, proof, over) : "";
        if (!proof.handed && proof.next !== "handed") { said += (said ? " " : "") + (over ? "It was never handed off." : "It hasn't been handed off yet."); }
        var li = add(el("li", proof.next && proofStuck(proof.next, proof) ? "stuck" : null), issue(text(unit.subject), unitName(unit)), el("span", "po-title", text(unit.title)),
          add(el("span", "po-next"), el("b", null, unitStanding(proof)), said ? " · " + said : null));
        li.setAttribute("tabindex", "-1");
        planRows[text(unit.subject)] = li;
        ul.appendChild(li);
      });
      nodes.push(ul);
    }
    var kinds = kindsOf();
    var amended = planUnits(plan.units);
    var approved = isObject(plan.approved) ? plan.approved : {};
    var frozen = planUnits(isObject(approved.snapshot) ? approved.snapshot.units : []);
    var shownUnits = planAsApproved ? frozen.map(function (unit) {
      var now = amended.filter(function (entry) { return entry.subject === unit.subject; })[0];
      return Object.assign({}, unit, { removed: !now || now.removed === true });
    }) : amended;
    var all = el("div", "plan-all");
    var switcher = el("div", "plan-switch");
    switcher.setAttribute("role", "group");
    switcher.setAttribute("aria-label", "Show the plan");
    [[false, "As amended"], [true, "As approved"]].forEach(function (choice) {
      var button = el("button", null, choice[1]);
      button.type = "button";
      button.setAttribute("aria-pressed", String(planAsApproved === choice[0]));
      button.addEventListener("click", function () { planAsApproved = choice[0]; drawPlan(shown); });
      switcher.appendChild(button);
    });
    switcher.appendChild(el("span", "ps-when", "Approved " + (spoken(approved.at) || "at an unknown time") + (text(approved.review) ? " · " + text(approved.review) : "")));
    add(all, switcher, add(el("div", "plan-grid-head"), el("div", null, "Unit"), el("div", null, "Criteria"), el("div", null, "Status")));
    var allProofs = planProofs(doc, shownUnits);
    shownUnits.forEach(function (unit) {
      var row = unitRow(allProofs[text(unit.subject)], kinds, planAsApproved);
      row.setAttribute("tabindex", "-1");
      planRows["all:" + text(unit.subject)] = row;
      all.appendChild(row);
    });
    var folds = el("div", "plan-folds");
    folds.appendChild(fold("units", "All units", shownUnits.length, all));
    if (review) { folds.appendChild(reviewFold(review)); }
    var changes = list(plan.changes).filter(isObject);
    var changeBox = el("div", "plan-changes");
    changeBox.appendChild(el("p", "waiting plan-scoped", scopeSentence(changes)));
    if (changes.length) { changeBox.appendChild(scopeChart(changes)); }
    var changeList = el("ul");
    changes.slice().reverse().forEach(function (change) { changeList.appendChild(changeRow(change)); });
    if (changes.length) { changeBox.appendChild(changeList); }  // with none, the sentence above says the scope is as approved
    if (isObject(PLAN.clipped)) {
      var clip = PLAN.clipped;
      changeBox.appendChild(el("p", "plan-clipped", "The plan was too large to show whole: " +
        [clip.changes ? "the text of the " + clip.changes + " oldest " + (clip.changes === 1 ? "change" : "changes") : "",
          clip.texts ? clip.texts + " other " + (clip.texts === 1 ? "text" : "texts") : ""].filter(Boolean).join(" and ") + " left out." +
        (clip.overShare ? " The approved plan alone is over the page's share for it." : "")));
    }
    folds.appendChild(fold("changes", "Changes since approval", changes.length, changeBox));
    var waivers = el("ul", "plan-waivers");
    var waivedCount = 0;
    counts.units.forEach(function (unit) {
      var proof = counts.proofs[text(unit.subject)];
      list(unit.proofs).forEach(function (kind) {
        if (proof.states[kind].state !== "waived") { return; }
        waivedCount += 1;
        waivers.appendChild(add(el("li"), el("b", null, unitName(unit) + " · " + kindLabel(kind)), " ", proof.states[kind].reason));
      });
    });
    folds.appendChild(fold("waived", "Waived proofs", waivedCount, waivedCount ? waivers : el("p", "waiting", "No proof was waived.")));
    if (Array.isArray(plan.approvals)) {
      var approvals = el("ul", "plan-approvals");
      var ahead = plan.approvals.filter(function (entry) { return isObject(entry) && entry.state === "waiting"; });
      plan.approvals.filter(isObject).forEach(function (entry) {
        approvals.appendChild(add(el("li", entry.state === "done" ? "done" : closedEntry(doc) ? "lapsed" : "waiting"), el("span", null, entry.state === "done" ? "Done" : closedEntry(doc) ? "Not recorded" : "Waiting"),
          " ", text(entry.text), text(entry.who) ? " · " + text(entry.who) : ""));
      });
      folds.appendChild(closedEntry(doc) ? fold("approvals", "Approvals", plan.approvals.filter(isObject).length, approvals) : fold("approvals", "Approvals ahead", ahead.length, approvals));
    }
    if (Array.isArray(plan.outOfScope)) {
      var scope = el("ul", "plan-scope-out");
      plan.outOfScope.forEach(function (entry) { scope.appendChild(el("li", null, text(entry))); });
      folds.appendChild(fold("out", "Out of scope", plan.outOfScope.length, scope));
    }
    folds.appendChild(fold("outcome", "The full outcome", null, el("p", "plan-full", text(isObject(plan.outcome) ? plan.outcome.text : ""))));
    nodes.push(folds);
    inner.replaceChildren.apply(inner, nodes);
  }

  /** In a stop's panel: where its unit stands, in the plan's own words, and separately a way to it. */
  function planStop(doc, subject) {
    if (!plan) { return null; }
    var unit = planUnits(plan.units).filter(function (entry) { return entry.subject === subject; })[0];
    if (!unit) { return null; }
    var proof = planProofs(doc, [unit])[subject];
    var removed = unit.removed === true;
    var box = add(el("div", "d-plan-row" + (!removed && proof.proven ? " proven" : removed ? " removed" : "") +
      (!removed && proof.next && proofStuck(proof.next, proof) ? " stuck" : "")),
      el("span", "pu-standing", removed ? "Removed from the plan" : unitStanding(proof)));
    if (!removed && proof.next) { box.appendChild(el("span", "d-plan-next", proofSaid(proof.next, proof, Boolean(closedEntry(doc))))); }
    var go = el("button", "d-plan", "See in the plan");
    go.type = "button";
    go.setAttribute("data-plan-unit", subject);
    go.addEventListener("click", function (event) {
      event.stopPropagation();
      var row = !proof.proven && unit.removed !== true ? planRows[subject] : null;
      if (!row) {
        planFolds.units = true;
        rememberFolds();
        planAsApproved = false;
        drawPlan(shown);
        row = planRows["all:" + subject];
      }
      if (row) {
        if (typeof row.scrollIntoView === "function") { row.scrollIntoView({ block: "center" }); }
        row.focus({ preventScroll: true });
      }
    });
    return add(box, go);
  }

  /* 5. What happened */
  // The story the narrator told of this record, which the renderer embedded and the live stream sends with each record.
  // The page draws it and words no entry itself: every head, line, label, mark and tab below comes from the story.
  var DEPTHS = [["headline", "Headlines"], ["story", "Story"], ["everything", "Everything"]];
  // What to show among the story, each the beats of one tone: the tabs for decisions, landings and problems, kept.
  var FILTERS = [["all", "All"], ["decisions", "Decisions"], ["landed", "Landings"], ["problems", "Setbacks"]];
  var FILTER_TONE = table({ decisions: "decision", landed: "landed", problems: "problem" });
  // A beat's mark, by its tone: the story says what kind of moment it is, and the page has one mark for each.
  var MARK_OF = table({ landed: "merge", ready: "ready", decision: "decide", problem: "stop", note: "note" });
  /** A tone's mark, as a beat and the legend both draw it; a tone the page doesn't know draws as a note. */
  function toneMark(tone) { return glyph(MARK_OF[tone] || "note"); }
  var DEPTH_KEY = (typeof VIEW.themeKey === "string" && VIEW.themeKey ? VIEW.themeKey.replace(/:theme$/, "") : "delivery-views") + ":story-depth";
  function storyOf(doc) {
    var story = isObject(doc.story) ? doc.story : null;
    return story && Array.isArray(story.chapters) && Array.isArray(story.beats) && Array.isArray(story.threads) && isObject(story.digest) ? story : null;
  }
  // The reader's depth: theirs once chosen, else Story while the delivery runs and Headlines once it has closed.
  function storyDepth(story) {
    var chosen = null;
    try { chosen = localStorage.getItem(DEPTH_KEY); } catch (error) { chosen = null; }
    if (DEPTHS.some(function (depth) { return depth[0] === chosen; })) { return chosen; }
    return storyDepthChosen || (story.state === "closed" ? "headline" : "story");
  }
  // The beat that tells one entry in full, by the entry's id, or null when the story holds none for it.
  var toldBy = typeof WeakMap === "function" ? new WeakMap() : null;
  function beatOf(doc, id) {
    var story = storyOf(doc);
    if (!story) { return null; }
    var found = toldBy && toldBy.get(story);
    if (!found) {
      found = Object.create(null);
      story.beats.forEach(function (beat) {
        if (isObject(beat) && beat.form !== "thread" && list(beat.altitudes).indexOf("everything") >= 0) {
          list(beat.entries).forEach(function (entry) { found[String(entry)] = beat; });
        }
      });
      if (toldBy) { toldBy.set(story, found); }
    }
    return found[String(id)] || null;
  }
  /**
   * An entry's words, as the story tells them: its own beat's, or, in a beat that tells several, its own line among
   * them. An entry the story doesn't hold, from a record told before it had one, has no words yet.
   */
  function entryWords(doc, entry) {
    var beat = beatOf(doc, entry.id);
    if (beat && beat.form === "entry") { return { head: text(beat.words.head), line: text(beat.words.line), label: text(beat.label) }; }
    var item = beat ? list(beat.items).filter(function (each) { return isObject(each) && each.entry === entry.id; })[0] : null;
    if (item) { return { head: text(item.words.head), line: text(item.words.line), label: text(beat.label) }; }
    if (beat) { return { head: text(beat.words.head), line: text(beat.words.line), label: text(beat.label) }; }
    return { head: "", line: "", label: "" };
  }
  /** What an entry says, as one line: its story line, or its head when its line is empty. */
  function toldLine(doc, entry) { var words = entryWords(doc, entry); return words.line || words.head; }
  /** A commit the words name, as a link to it on the forge when the page was given its address: cut short to read. */
  function commitRef(sha) {
    var href = commitHref(sha);
    var commit = el(href ? "a" : "code", "fact-ref", short(sha));
    if (href) {
      commit.setAttribute("href", href);
      commit.setAttribute("target", "_blank");
      commit.setAttribute("rel", "noopener noreferrer");
      commit.setAttribute("aria-label", "Open commit " + short(sha));
    }
    return commit;
  }
  /**
   * The story's words, made readable without changing what they say: each ticket id they name links to its ticket, so
   * a reader can open it or see its card as anywhere else on the page; a full commit id is cut short and opens on the
   * forge; and a change request's address shows as its number. The ids are the record's own, the ones the renderer gave
   * addresses for.
   */
  function linked(words) {
    var said = text(words);
    var ids = Object.keys(LINKS.issues || {}).filter(function (id) { return id && said.indexOf(id) >= 0; })
      .sort(function (a, b) { return b.length - a.length; });
    var tickets = ids.length ? "|(?:^|[^\\w-])(" + ids.map(function (id) { return id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|") + ")(?![\\w-])" : "";
    // A commit is named in full or cut short; a short one links only when it begins exactly one commit the page knows.
    var pattern = new RegExp("(\\b[0-9a-f]{7,40}\\b)|(https?:\\/\\/[^\\s)]+)" + tickets, "g");
    var nodes = [], last = 0, found;
    while ((found = pattern.exec(said))) {
      var match = found[1] || found[2] || found[3];
      var at = found.index + found[0].indexOf(match);
      if (at > last) { nodes.push(said.slice(last, at)); }
      if (found[1]) {
        var named = found[1].length === 40 ? [found[1]] : Object.keys(LINKS.commits || {}).filter(function (sha) { return sha.indexOf(found[1]) === 0; });
        nodes.push(named.length === 1 ? commitRef(named[0]) : found[1]);
      } else if (found[2]) {
        // Punctuation that ends the sentence is not part of the address.
        var address = found[2].replace(/[.,;:]+$/, "");
        nodes.push(changeTarget(address) ? changeLink(address) : address);
        if (address.length < found[2].length) { nodes.push(found[2].slice(address.length)); }
      } else {
        nodes.push(issue(found[3]));
      }
      last = at + match.length;
    }
    if (last < said.length) { nodes.push(said.slice(last)); }
    return nodes;
  }
  function timeOf(instant) {
    var parts = clockParts(instant);
    var time = parts ? add(el("time"), el("span", "t-clock", parts.time), parts.day ? el("span", "t-day", parts.day) : null) : el("time");
    time.setAttribute("datetime", text(instant));
    if (parts) { time.setAttribute("aria-label", clock(instant)); }
    return time;
  }
  /** One beat as a row: its time, its mark and its words in full. Its ticket, commit and change request are links in the words. */
  function beatRow(doc, story, beat, fresh, depth) {
    // The kinds whose line counts a list its entry holds, which the beat's fold then names in full: the checks chosen, and
    // the lead's notes on a hand-off, which may name the checks too.
    var FOLDED_LISTS = { "check.selected": "checks", "handoff.checked": "notes" };
    function folded(doc, beat) {
      var field = beat.form === "entry" ? FOLDED_LISTS[text(beat.kind)] : null;
      if (!field) { return []; }
      var entry = list(doc.journal).filter(function (each) { return isObject(each) && each.id === list(beat.entries)[0]; })[0];
      var named = entry && isObject(entry.data) ? list(entry.data[field]) : [];
      return named.every(function (name) { return typeof name === "string"; }) ? named : [];
    }
    /**
     * Where a row sits on its setback's margin line, from the thread's own facts: its state, its opening and its closing.
     * The line runs from a ring on the row holding the opening to a tick on the row holding the closing, so two setbacks
     * back to back read as two; rows of the thread before its opening or after its closing stand clear of it. It is held
     * while the thread is open and the page's hairline once it closed.
     */
    function threadMarks(li, beat, thread) {
      var order = list(thread.entries), mine = list(beat.entries);
      var opens = mine.indexOf(thread.opening) >= 0, closes = thread.closing !== null && thread.closing !== undefined && mine.indexOf(thread.closing) >= 0;
      var at = order.indexOf(mine[0]), from = order.indexOf(thread.opening);
      var to = thread.closing === null || thread.closing === undefined ? order.length : order.indexOf(thread.closing);
      li.classList.add(thread.state === "open" ? "t-open" : "t-closed");
      if (opens || closes || (from >= 0 && at > from && at < to)) { li.classList.add("t-span"); }
      if (opens) { li.classList.add("t-opens"); li.appendChild(el("span", "thread-ring")); }
      if (closes) { li.classList.add("t-closes"); li.appendChild(el("span", "thread-tick")); }
    }
    var threads = story.threads.filter(function (thread) { return isObject(thread) && thread.id === beat.thread; });
    // Below the headline a setback's entries keep their places in time, tied by a thin line in the margin.
    var li = el("li", "beat is-" + text(beat.tone) + (beat.thread && beat.form !== "thread" && depth !== "headline" ? " threaded" : "") + (fresh ? " fresh" : ""));
    li.setAttribute("data-beat", text(beat.id));
    if (beat.thread) { li.setAttribute("data-thread", text(beat.thread)); }
    if (beat.form === "thread" && threads.length) { return setbackSheet(doc, beat, threads[0], li); }
    if (threads.length && beat.form !== "thread" && depth !== "headline") { threadMarks(li, beat, threads[0]); }
    var words = add(el("span", "beat-words"), add.apply(null, [el("span", "beat-said")].concat(linked(beat.words.head))),
      text(beat.words.line) ? add.apply(null, [el("span", "beat-line")].concat(linked(beat.words.line))) : null);
    var body = el("div", "beat-body");
    if (beat.form === "bookkeeping") {
      // The one run that folds: opened, it lists each of its entries in that entry's own words.
      var listed = el("ul", "beat-items");
      list(beat.items).forEach(function (item) {
        if (isObject(item) && isObject(item.words)) {
          listed.appendChild(add(el("li"), text(item.words.head), text(item.words.line) ? el("span", "beat-line", " " + text(item.words.line)) : null));
        }
      });
      var fold = add(el("details", "beat-fold"), add(el("summary", "beat-head"), words), listed);
      body.appendChild(runFold(fold, { key: "beat:" + text(beat.id) + ":" + list(beat.entries)[0] }));
    } else if (folded(doc, beat).length) {
      // A beat whose line counts what its entry lists folds the list: opened, it names each, one to a line.
      var names = add.apply(null, [el("ul", "beat-items beat-names")].concat(folded(doc, beat).map(function (name) { return el("li", null, name); })));
      body.appendChild(runFold(add(el("details", "beat-fold"), add(el("summary", "beat-head"), words), names),
        { key: "beat:" + text(beat.id) + ":" + list(beat.entries)[0] }));
    } else {
      body.appendChild(add(el("div", "beat-head"), words));
    }
    add(li, timeOf(beat.at), toneMark(beat.tone), add(body, el("span", "beat-label", text(beat.label))));
    return li;
  }
  /**
   * A setback, told once: what broke, why, what it held up and how it ended, on one quiet sheet whose only number is
   * its cost. One still standing says since when, and breathes on the page's slow pulse.
   */
  function setbackSheet(doc, beat, thread, li) {
    li.className = "beat setback " + (thread.state === "open" ? "is-standing" : "is-closed");
    var told = isObject(thread.told) ? thread.told : {};
    var sheet = el("div", "setback-sheet");
    var words = add(el("div", "setback-words"), add.apply(null, [el("div", "setback-head")].concat(linked(beat.words.head))));
    // Its cause, its own line, what it held up and how it ended, each a sentence that says which it is.
    [told.cause, told.said, told.heldUp, told.ending].forEach(function (part) {
      if (text(part)) { words.appendChild(add.apply(null, [el("p", "setback-part")].concat(linked(part)))); }
    });
    var cost = thread.state === "closed" && typeof thread.cost === "number"
      ? add(el("div", "setback-cost"), el("b", null, duration(thread.cost)), el("span", null, (clock(thread.openedAt) || "") + " to " + (clock(thread.closedAt) || "")))
      : add(el("div", "setback-cost is-standing"), el("b", null, "Still standing"), el("span", null, "since " + (clock(thread.openedAt) || "a time unknown")));
    add(li, timeOf(beat.at), toneMark(beat.tone), add(el("div", "beat-body"), add(el("div", "beat-head"), add(sheet, words, cost))));
    return li;
  }
  // Exactly the beats the story holds at a depth, and among them the tone the reader chose.
  function beatShown(beat, depth, filter) {
    if (!isObject(beat) || list(beat.altitudes).indexOf(depth) < 0) { return false; }
    return filter === "all" || beat.tone === FILTER_TONE[filter];
  }
  /** What each mark on the page means: one entry for each tone among the beats in view, in the story's words. */
  function storyLegend(story, depth) {
    var present = Object.create(null);
    story.beats.forEach(function (beat) { if (beatShown(beat, depth, storyFilter)) { present[text(beat.tone)] = true; } });
    var legend = el("div", "story-legend");
    list(story.tones).filter(function (tone) { return isObject(tone) && present[text(tone.tone)]; }).forEach(function (tone) {
      legend.appendChild(add(el("span"), toneMark(text(tone.tone)), text(tone.word)));
    });
    return legend.childNodes.length ? legend : null;
  }
  function drawHappened(doc) {
    var inner = parts.happened;
    var from = runHeldHeight(inner);
    var story = storyOf(doc);
    if (!story) {
      inner.replaceChildren(el("div", "kicker", "What happened"), el("h2", "title", "The story could not be told from this record."));
      return;
    }
    var depth = storyDepth(story);
    var seen = Object.create(null);
    story.beats.forEach(function (beat) { if (isObject(beat)) { seen[beatKey(beat)] = true; } });
    var switches = add(el("div", "story-bar"), choice("Depth", DEPTHS, depth, function (picked) {
      storyDepthChosen = picked;
      try { localStorage.setItem(DEPTH_KEY, picked); } catch (error) { /* chosen until the page reloads */ }
    }, "depth"), choice("Show", FILTERS, storyFilter, function (picked) { storyFilter = picked; }, "tab"));
    var beats = Object.create(null);
    story.beats.forEach(function (beat) { if (isObject(beat)) { beats[text(beat.id)] = beat; } });
    var ol = el("ol", "story");
    story.chapters.slice().reverse().forEach(function (chapter) {
      if (!isObject(chapter) || !isObject(chapter.words)) { return; }
      var shownBeats = list(chapter.beats).map(function (name) { return beats[text(name)]; })
        .filter(function (beat) { return beatShown(beat, depth, storyFilter); }).reverse();
      var standing = list(chapter.words.standing).filter(isObject);
      if (storyFilter !== "all" && !shownBeats.length && !(storyFilter === "problems" && standing.length) && !(storyFilter === "landed" && chapter.form === "landing")) { return; }
      var li = el("li", "chapter-of is-" + text(chapter.form));
      li.setAttribute("data-chapter", text(chapter.id));
      var heading = add(el("div", "chapter-head"), add.apply(null, [el("h3")].concat(linked(chapter.words.head))), timeOf(chapter.at));
      li.appendChild(heading);
      // The piece that landed, by its title, then how long it took to land; each on its own line.
      if (text(chapter.pieceTitle)) { li.appendChild(el("p", "chapter-line", text(chapter.pieceTitle))); }
      if (text(chapter.words.line)) { li.appendChild(el("p", chapter.pieceTitle ? "chapter-when" : "chapter-line", text(chapter.words.line))); }
      standing.forEach(function (carried) {
        li.appendChild(add.apply(null, [el("p", "chapter-standing" + (carried.until ? " is-ended" : ""))].concat(linked(carried.text),
          [carried.until ? ", until " + (clock(carried.until) || "a time unknown") + "." : ""])));
      });
      if (shownBeats.length) {
        var ul = el("ul", "beats");
        shownBeats.forEach(function (beat) { ul.appendChild(beatRow(doc, story, beat, prev.story && !prev.story[beatKey(beat)] && !reduced(), depth)); });
        li.appendChild(ul);
      }
      ol.appendChild(li);
    });
    if (!ol.childNodes.length) { ol.appendChild(el("li", "waiting", "Nothing here yet.")); }
    prev.story = seen;
    var digest = isObject(story.digest.words) ? story.digest.words : { head: "", line: "" };
    // A part the story doesn't hold, a digest line or a legend, is left out rather than drawn as nothing.
    inner.replaceChildren();
    add(inner, el("div", "kicker", "What happened"), el("h2", "title", text(digest.head)),
      text(digest.line) ? el("p", "say", text(digest.line)) : null, storyLegend(story, depth), switches, ol);
    // Depth changes density in place: the chapter eases from the height it had, on the shared fold's motion.
    runEaseHeight(inner, from);
  }
  function beatKey(beat) { return text(beat.form) + ":" + list(beat.entries).join(","); }
  /** A small group of buttons choosing one of its options, drawn again on a pick. */
  function choice(label, options, current, pick, data) {
    var group = el("div", "tabs" + (data === "depth" ? " depth" : " filter"));
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", label);
    options.forEach(function (option) {
      var button = el("button", null, option[1]);
      button.type = "button";
      button.setAttribute("aria-pressed", String(current === option[0]));
      button.setAttribute("data-" + data, option[0]);
      button.addEventListener("click", function () { var focus = focusMark(); pick(option[0]); drawHappened(shown); refocus(focus); });
      group.appendChild(button);
    });
    return group;
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
      Object.keys(parts).filter(function (name) { return name !== "now"; }).forEach(function (name) { parts[name].replaceChildren(); });
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
    if (parts.plan) { drawPlan(doc); }
    drawHappened(doc);
    drawParked(doc);
    dropTurned();
    refocus(focus);
    keepPace();
  }
  /**
   * A redraw rebuilds the marks that turn or pulse, and a rebuilt mark would start its turn over, which reads as a
   * stutter every time the record is written or the clock ticks. Each looping animation is set to the page's own clock
   * instead, so a mark drawn again carries on from where the last one was.
   */
  function onPace(animation) {
    var timing = animation.effect && typeof animation.effect.getTiming === "function" ? animation.effect.getTiming() : null;
    // A mark held still where it was, as a settling check's is, is left there.
    if (!timing || timing.iterations !== Infinity || !animation.animationName || animation.startTime === 0 || animation.playState === "paused") { return; }
    try { animation.startTime = 0; } catch (error) { /* an animation that cannot be moved keeps its own start */ }
  }
  function keepPace() {
    if (typeof document.getAnimations === "function") { document.getAnimations().forEach(onPace); }
  }
  // A mark drawn outside a full redraw, as a stop's detail or a check's row is, joins the same clock as it starts, before
  // its first frame is painted.
  if (typeof document.addEventListener === "function") {
    document.addEventListener("animationstart", function (event) {
      var node = event.target;
      if (node && typeof node.getAnimations === "function") { node.getAnimations({ subtree: true }).forEach(onPace); }
    }, true);
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
  // Each tick also re-reads the served page: a supervisor's pass rewrites its Needs you list without a record push.
  if (typeof setInterval === "function") { setInterval(function () { refreshRuns(); safely(lastGood || shown); }, 30000); }
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
