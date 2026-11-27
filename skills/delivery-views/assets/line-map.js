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
// On a phone the corner's person icon stands for whose checkout drew the page; a tap shows the address, and a tap
// anywhere else, or Escape, puts it away.
(function () {
  "use strict";
  var mark = document.querySelector(".operator-mark");
  if (!mark) { return; }
  var holder = mark.parentNode;
  function show(open) {
    holder.classList.toggle("open", open);
    mark.setAttribute("aria-expanded", String(open));
  }
  mark.addEventListener("click", function (event) {
    event.stopPropagation();
    show(mark.getAttribute("aria-expanded") !== "true");
  });
  document.addEventListener("click", function (event) {
    if (!holder.contains(event.target)) { show(false); }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && mark.getAttribute("aria-expanded") === "true") { show(false); mark.focus(); }
  });
})();
(function () {
  "use strict";
  var embedded = null;
  try { embedded = JSON.parse(document.getElementById("delivery-state").textContent); } catch (error) { embedded = null; }
  var app = document.getElementById("app");
  var connection = "saved";
  var shown = embedded;
  // The stop the reader picked, by its subject: a click, Enter, Space or keyboard focus picks; pointing never does.
  var picked = page.state("line:selected");
  function selected() { var key = picked.get(); return key === undefined ? null : key; }
  // Every control the page draws carries its key, page.key(kind, id): the record's own id for what it stands for, never
  // its place on the page. A redraw puts focus back on the same key (page.js).
  function withKey(node, kind, id) {
    if (node && id !== undefined && id !== null && String(id) !== "") { node.setAttribute("data-key", page.key(kind, id)); }
    return node;
  }
  // The control that opened the detail panel, by its key: Escape in the panel returns focus to it.
  var opener = page.state("line:opener");
  if (typeof document.addEventListener === "function") {
    document.addEventListener("keydown", function (event) {
      var active = document.activeElement;
      if (event.key !== "Escape" || !active || !active.closest || !active.closest("#detail")) { return; }
      var key = opener.get();
      // Matched by attribute value, never by a selector built from record text; the opener on the line, not in the panel.
      var back = Array.prototype.filter.call(app.querySelectorAll("[data-key]"), function (node) {
        return node.getAttribute("data-key") === key && !node.closest("#detail");
      })[0];
      if (back) { event.preventDefault(); back.focus({ preventScroll: true }); }
    });
  }
  // The lane the pointer or focus lights on the line, kept through a redraw.
  var lit = page.state("line:lit");
  // The page of a run's checks the reader turned to, with the run it belongs to.
  var checksRead = page.state("line:checks-page");
  // What the reader picked to show of the story: a tone to filter by, and a depth this visit, until it is remembered.
  // What the page last showed, so a change can be animated rather than just swapped in.
  var prev = { figures: Object.create(null), runningCheck: null, stages: null, story: null, progress: null, lanes: null, byLane: null, parked: null, dots: null, selected: null,
    checkingHeight: null, checkPage: null };
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
  // Live-update motion asks the page's one motion check (page.js) as not the reader's, so a keyboard reader sees it too.
  function reduced() { return !page.motion(false); }
  function later(callback) {
    if (typeof requestAnimationFrame === "function") { requestAnimationFrame(function () { requestAnimationFrame(callback); }); } else { callback(); }
  }
  function replay(node, cls) { node.classList.remove(cls); void node.offsetWidth; node.classList.add(cls); }
  /**
   * Words holding numbers, put into `node`. A number that changed since this place on the page was last drawn flips
   * its changed digits over from the old ones, lined up from the right as on a counter, and a digit with none before
   * it flips in from blank. The words stay one plain text, the new value, so the line never moves: while a digit turns,
   * a highlight hides it and a layer drawn over its place, with no words of its own, rolls the old digit out and the
   * new one in. A first draw, an unchanged number, words that now hold more numbers or fewer, a reader who asked for
   * less motion and a browser without highlights get the words as they are.
   */
  function figures(node, key, words) {
    var was = prev.figures[key];
    prev.figures[key] = words;
    node.textContent = words;
    var registry = highlights();
    if (typeof was !== "string" || was === words || reduced() || !registry) { return node; }
    // Words that now hold more numbers or fewer say something else, and their numbers no longer pair up: no flip.
    var old = was.match(/\d+/g) || [];
    if (old.length !== (words.match(/\d+/g) || []).length) { return node; }
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
    // The layers are drawing, not words: the drawing's ticks and steps are read one by one, and its counts once.
    spell(shape(svg, "text", Object.assign({}, attributes, { class: attributes.class + " flip-out", "aria-hidden": "true" })), was, changed, "flip-hide");
    spell(shape(svg, "text", Object.assign({}, attributes, { class: attributes.class + " flip-in", "aria-hidden": "true" })), words, changed, "flip-hide");
    return node;
  }

  // How the page is configured, and the links the configuration allows, worked out by the renderer: the page
  // links a ticket id, a change request or a fix commit only when the renderer gave it an address.
  var VIEW = {};
  try { VIEW = JSON.parse(document.getElementById("delivery-view").textContent); } catch (error) { VIEW = {}; }
  if (!isObject(VIEW)) { VIEW = {}; }
  var LINKS = { issues: {}, changes: {}, commits: {}, files: {} };
  function readLinks(value) {
    if (!isObject(value)) { return; }
    LINKS = { issues: table(isObject(value.issues) ? value.issues : {}), changes: table(isObject(value.changes) ? value.changes : {}),
      commits: table(isObject(value.commits) ? value.commits : {}), files: table(isObject(value.files) ? value.files : {}) };
  }
  try { readLinks(JSON.parse(document.getElementById("delivery-links").textContent)); } catch (error) { /* nothing is linked */ }
  function issueHref(id) { return typeof LINKS.issues[id] === "string" ? LINKS.issues[id] : null; }
  // The key of the follow-up the close-out filed for one of a story's deferred findings, which the record keeps as
  // "<story>-<finding>" once the tracker names it; null until then.
  function followUpOf(doc, subject) {
    return function (finding) {
      var id = String(subject) + "-" + String(finding);
      var entry = list(doc && doc.state ? doc.state.followups : null).filter(function (item) { return isObject(item) && item.id === id; })[0];
      return entry && typeof entry.reference === "string" && entry.reference && entry.reference !== id ? entry.reference : null;
    };
  }
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
  // The seat the local page is drawn for, as the renderer embedded it on a shared record. The hosted page has none, and
  // names every owner by label.
  var viewerBlock = document.getElementById("delivery-viewer");
  var VIEWER = viewerBlock ? text(readNeeds(viewerBlock.textContent).seat) || null : null;
  // What each lane has pushed, as the supervisor's branch read wrote it beside the record: { at, lanes, files, more },
  // or { unreadable }. With no block no pass has read the branches yet.
  var filesBlock = document.getElementById("delivery-files");
  var FILES = filesBlock ? readNeeds(filesBlock.textContent) : null;
  // What a closed delivery left open, as the renderer derived it from the record and said it from the table.
  var leftBlock = document.getElementById("delivery-left-open");
  var LEFT_OPEN = leftBlock ? readNeeds(leftBlock.textContent) : null;
  // What the forge says of the integration's change request, as the renderer read it beside the record:
  // { checks: { pending, failing: [{ name, onTarget }] } }. Without it Now says nothing of the forge's checks.
  var integrationBlock = document.getElementById("delivery-integration");
  var FORGE = integrationBlock ? readForge(integrationBlock.textContent) : null;
  function readForge(textContent) {
    try { var block = JSON.parse(textContent); } catch (error) { return null; }
    var checks = isObject(block) && isObject(block.checks) ? block.checks : null;
    var count = function (value) { return typeof value === "number" && value >= 0 && Math.floor(value) === value; };
    return checks && count(checks.pending) && Array.isArray(checks.failing) ? { checks: checks } : null;
  }
  function changeLink(changeRequest) {
    var target = changeTarget(changeRequest);
    if (!target) { return el("span", "pr-num", text(changeRequest)); }
    var link = withKey(el("a", "issue-link pr-num", target.label), "request", target.label);
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
  var WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  function twoDigits(n) { return (n < 10 ? "0" : "") + n; }
  // A moment as a person says it, in local time: "Today at 4:54 pm", "Yesterday at 9:02 am", "Tue, Nov 18 at 4:54 pm",
  // with the year only when it isn't this one.
  function spokenMoment(at) {
    var date = new Date(at * 1000), today = new Date(Date.now());
    var clock = ((date.getHours() + 11) % 12 + 1) + ":" + twoDigits(date.getMinutes()) + (date.getHours() < 12 ? " am" : " pm");
    var yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    var day = date.toDateString() === today.toDateString() ? "Today"
      : date.toDateString() === yesterday.toDateString() ? "Yesterday"
      : WEEKDAYS[date.getDay()] + ", " + MONTHS[date.getMonth()] + " " + date.getDate()
        + (date.getFullYear() !== today.getFullYear() ? ", " + date.getFullYear() : "");
    return day + " at " + clock;
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
  /** A chapter's section and its link on the rail, placed before another chapter's when one is named. */
  function chapterOf(chapter, before) {
    var section = el("section", "chapter chapter-" + chapter[0]);
    section.id = chapter[0];
    var inner = el("div", "inner");
    section.appendChild(inner);
    parts[chapter[0]] = inner;
    var link = withKey(el("a"), "chapter", chapter[0]);
    link.setAttribute("href", "#" + chapter[0]);
    link.setAttribute("aria-label", chapter[1]);
    add(link, el("span", null, chapter[1]), el("i"));
    var next = before && parts[before] ? parts[before].parentNode : null;
    app.insertBefore(section, next);
    rail.insertBefore(link, next ? rail.querySelector('a[href="#' + before + '"]') : null);
    // A chapter made after the page was built is watched like the rest; the first ones are watched once all are made.
    if (watch) { watch.observe(section); }
    if (arrive) { arrive.observe(section); }
    return section;
  }
  CHAPTERS.forEach(function (chapter) { chapterOf(chapter); });
  // The history page, when the binding declares one: served live at /view/<output>, beside the page as a file.
  // It sits in the chapter tab, below the chapters (see the corner's tab).
  var historyHref = typeof VIEW.history === "string" && VIEW.history
    ? (typeof location === "object" && /^https?:$/.test(location.protocol) ? "/view/" : "") + VIEW.history.split("/").map(encodeURIComponent).join("/")
    : null;
  if (typeof IntersectionObserver === "function") {
    var watch = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        // Only a new chapter is followed, so a deep link into the chapter being read keeps its item; following it shows
        // the chapter, which marks it on the rail.
        if (!entry.isIntersecting || PAGED || entry.target.id === page.here().chapter) { return; }
        page.go({ chapter: entry.target.id }, "follow");
      });
    }, { rootMargin: "-45% 0px -45% 0px" });
    Array.prototype.forEach.call(app.querySelectorAll(".chapter"), function (section) { watch.observe(section); });
    // The longest entrance: .7s of fade after the last row's .3s delay, with a little to spare.
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

  // Pages: each chapter is its own page, routed by the address's hash (#now, #line, ...). A hash that names something
  // inside a chapter opens that chapter's page and brings the thing into view. The rail, the corner's chapter tab,
  // scrolling on past a page's end and the Left and Right arrow keys step between pages. With no hash the page opens on Now.
  // The reader picks the layout in the chapter tab: one chapter per page (the default) or every chapter on one page.
  var LAYOUT_KEY = (typeof VIEW.themeKey === "string" && VIEW.themeKey ? VIEW.themeKey.replace(/:theme$/, "") : "delivery-views") + ":layout";
  var layoutPick = page.state(LAYOUT_KEY, { saved: true });
  var PAGED = layoutPick.get() !== "scroll";
  var currentPage = null;
  var corner = document.querySelector(".corner");
  var themeSwitch = document.querySelector(".theme-switch");
  if (!corner && themeSwitch) {
    // The hosted page has no operator mark, so the switch stands alone; the tab joins it in a corner group.
    corner = el("div", "corner");
    themeSwitch.parentNode.insertBefore(corner, themeSwitch);
    corner.appendChild(themeSwitch);
  }
  var tab = el("div", "chapter-tab");
  var tabButton = el("button", "chapter-tab-button");
  tabButton.type = "button";
  tabButton.setAttribute("aria-haspopup", "true");
  tabButton.setAttribute("aria-expanded", "false");
  tabButton.setAttribute("aria-controls", "chapter-tab-list");
  var tabIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  tabIcon.setAttribute("viewBox", "0 0 24 24");
  tabIcon.setAttribute("aria-hidden", "true");
  tabIcon.innerHTML = '<path d="M9 6h11M9 12h11M9 18h11M5 6h.01M5 12h.01M5 18h.01"/>';
  // The label holds every chapter's name in one cell, only the current one seen, so the tab is as wide as the longest
  // name and stepping between pages never moves the corner.
  var tabLabel = el("span", "chapter-tab-label");
  var tabName = el("span", "chapter-tab-name", "Now");
  tabLabel.appendChild(tabName);
  function nameTab(id) {
    tabName.textContent = chapterName(id);
    var sizers = chapterIds().map(function (one) {
      var sizer = el("span", "chapter-tab-sizer", chapterName(one));
      sizer.setAttribute("aria-hidden", "true");
      return sizer;
    });
    tabLabel.replaceChildren.apply(tabLabel, [tabName].concat(sizers));
  }
  var tabChevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  tabChevron.setAttribute("viewBox", "0 0 24 24");
  tabChevron.setAttribute("aria-hidden", "true");
  tabChevron.setAttribute("class", "chapter-tab-chevron");
  tabChevron.innerHTML = '<path d="M6 9l6 6l6 -6"/>';
  add(tabButton, tabIcon, tabLabel, tabChevron);
  var tabList = el("ol", "chapter-tab-list");
  tabList.id = "chapter-tab-list";
  tabList.hidden = true;
  var tabPanel = el("div", "chapter-tab-panel");
  var layoutButton = el("button", "chapter-tab-layout");
  layoutButton.type = "button";
  var hint = el("span", "chapter-tab-hint");
  hint.innerHTML = "<kbd>←</kbd> <kbd>→</kbd> step between pages";
  var historyItem = null;
  if (historyHref) {
    historyItem = el("a", "chapter-tab-history");
    historyItem.setAttribute("href", historyHref);
    // Named apart from the page's clock(), which a var here would replace for the whole page.
    var historyIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    historyIcon.setAttribute("viewBox", "0 0 24 24");
    historyIcon.setAttribute("aria-hidden", "true");
    historyIcon.innerHTML = '<path d="M3 12a9 9 0 1 0 3 -6.7"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/>';
    add(historyItem, historyIcon, el("span", null, "Past deliveries and runs"));
  }
  var tabFoot = add(el("div", "chapter-tab-foot"), layoutButton, hint);
  add(tab, tabButton, tabList);
  if (corner) { corner.insertBefore(tab, corner.querySelector(".theme-switch")); }
  // The delivery's name stays in the top-left corner on every page but Now, which opens with it.
  var where = el("a", "page-where");
  where.setAttribute("href", "#now");
  // The line map in small: three lanes running into one line, the page's own mark for going back to the start.
  var mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  mark.setAttribute("viewBox", "0 0 22 14");
  mark.setAttribute("aria-hidden", "true");
  mark.setAttribute("class", "page-where-mark");
  mark.innerHTML = '<path class="w1" d="M1 2.5h5c3 0 4 4.5 7 4.5"/><path class="w2" d="M1 7h12"/><path class="w3" d="M1 11.5h5c3 0 4 -4.5 7 -4.5"/>' +
    '<path class="w0" d="M13 7h8"/><circle class="wd" cx="13" cy="7" r="1.6"/>';
  var whereTitle = el("span", "page-where-title");
  var whereNote = el("span", "page-where-note");
  add(where, mark, whereTitle, whereNote);
  document.body.appendChild(where);
  /** The delivery's name in the corner: its title, and a closing parenthetical (its status) set apart. */
  function fillWhere(name) {
    var words = name.replace(/\s+/g, " ").trim();
    var note = /^(.*\S)\s*\(([^()]{1,80})\)$/.exec(words);
    whereTitle.textContent = note ? note[1] : words;
    whereNote.textContent = note ? note[2] : "";
    where.setAttribute("aria-label", (note ? note[1] : words) + ", back to the start");
  }

  // The chapters that are pages now: one a record has emptied (Across repositories, with no sibling left) is no page.
  function chapterIds() {
    return Array.prototype.filter.call(app.querySelectorAll(".chapter"), function (section) { return !section.classList.contains("is-absent"); })
      .map(function (section) { return section.id; });
  }
  function chapterName(id) {
    var link = rail.querySelector('a[href="#' + id + '"]');
    return link ? link.getAttribute("aria-label") : id;
  }
  function fillTab() {
    var ids = chapterIds();
    tabList.replaceChildren();
    ids.forEach(function (id, index) {
      var link = el("a");
      link.setAttribute("href", "#" + id);
      if (id === currentPage) { link.setAttribute("aria-current", "page"); }
      add(link, el("span", "chapter-tab-index", String(index + 1).padStart(2, "0")), el("span", null, chapterName(id)));
      tabList.appendChild(add(el("li"), link));
    });
    layoutButton.textContent = PAGED ? "Show all chapters on one page" : "Show one chapter per page";
    if (historyItem) { tabList.appendChild(add(el("li", "chapter-tab-history-row"), historyItem)); }
    tabList.appendChild(add(el("li", "chapter-tab-foot-row"), tabFoot));
  }
  function openTab(open) {
    tabList.hidden = !open;
    tabButton.setAttribute("aria-expanded", String(open));
    if (open) {
      fillTab();
      var current = tabList.querySelector('[aria-current="page"]') || tabList.querySelector("a");
      if (current) { current.focus(); }
    }
  }
  tabButton.addEventListener("click", function () { openTab(tabList.hidden); });
  tabList.addEventListener("click", function (event) { if (event.target.closest && event.target.closest("a")) { openTab(false); } });
  // Filled once now, shut, so the page holds its links (the history page among them) before the tab is ever opened.
  fillTab();
  layoutButton.addEventListener("click", function () {
    PAGED = !PAGED;
    layoutPick.set(PAGED ? "paged" : "scroll");
    page.go({ chapter: currentPage }, "layout");
    openTab(false);
    tabButton.focus();
  });
  tabList.addEventListener("keydown", function (event) {
    var links = Array.prototype.slice.call(tabList.querySelectorAll("a"));
    var at = links.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      var next = links[(at + (event.key === "ArrowDown" ? 1 : links.length - 1)) % links.length];
      if (next) { next.focus(); }
    }
  });
  document.addEventListener("click", function (event) { if (!tab.contains(event.target)) { openTab(false); } });
  /** Whether a control the focus is in uses the Left and Right arrows itself: the chapter tab's list, or a region that scrolls sideways. */
  function arrowsOwned(node) {
    for (var at = node; at && at.nodeType === 1 && at !== document.body; at = at.parentNode) {
      if (at === tabList) { return true; }
      var style = typeof getComputedStyle === "function" ? getComputedStyle(at) : null;
      if (style && /^(auto|scroll)$/.test(style.overflowX) && at.scrollWidth > at.clientWidth) { return true; }
    }
    return false;
  }
  document.addEventListener("keydown", function (event) {
    if (launcherOpen()) { return; }
    if (event.key === "Escape" && !tabList.hidden) { openTab(false); tabButton.focus(); return; }
    var typing = event.target && (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable === true);
    if (!PAGED || typing || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) { return; }
    // The Left and Right arrows turn to the previous and next page, unless the control in focus uses them.
    if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && !arrowsOwned(event.target)) {
      var ids = chapterIds();
      var to = ids[ids.indexOf(currentPage) + (event.key === "ArrowRight" ? 1 : -1)];
      if (to) { event.preventDefault(); page.go({ chapter: to }, "turn"); }
    }
  });

  /**
   * Shows one chapter's page, and never scrolls: the page controller (page.js) moves the page, by the navigation's
   * reason, and calls this as it does.
   */
  function showPage(id) {
    var ids = chapterIds();
    if (ids.indexOf(id) < 0) { id = ids[0]; }
    currentPage = id;
    document.body.classList.toggle("paged", PAGED);
    Array.prototype.forEach.call(app.querySelectorAll(".chapter"), function (section) {
      var on = section.id === id;
      section.hidden = section.classList.contains("is-absent") || (PAGED && !on);
    });
    Array.prototype.forEach.call(rail.querySelectorAll("a"), function (link) {
      link.setAttribute("aria-current", String(link.getAttribute("href") === "#" + id));
    });
    nameTab(id);
    tabButton.setAttribute("aria-label", "Chapters: " + chapterName(id));
    var heading = document.querySelector("h1");
    fillWhere(heading ? heading.textContent : document.title);
    where.hidden = id === ids[0];
    document.title = (heading ? heading.textContent : document.title.split(" · ").pop()).replace(/\s+/g, " ") +
      (id === ids[0] ? "" : " · " + chapterName(id));
  }

  // The launcher: one place to reach anything on the page by name. It is a registry of commands, each
  // { id, group, title, detail?, keywords?, current?(), run() }, read afresh each time it opens, so a feature adds what
  // it offers with launcher.add(...) and nothing else changes. Opened with Cmd/Ctrl-K, with / when not typing, or from
  // the corner; arrows move, Enter runs, Escape closes and gives focus back.
  var launcher = (function () {
    var sources = [];
    var shown = [];
    var active = 0;
    var before = null;
    var root = el("div", "launcher");
    root.hidden = true;
    var scrim = el("div", "launcher-scrim");
    var panel = el("div", "launcher-panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Go to");
    var field = el("div", "launcher-field");
    var glass = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    glass.setAttribute("viewBox", "0 0 24 24");
    glass.setAttribute("aria-hidden", "true");
    glass.innerHTML = '<path d="M10.5 18a7.5 7.5 0 1 0 0 -15a7.5 7.5 0 0 0 0 15z"/><path d="M21 21l-5.2 -5.2"/>';
    var input = el("input", "launcher-input");
    input.type = "text";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", "launcher-list");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.placeholder = "Go to a page…";
    var esc = el("kbd", "launcher-esc", "esc");
    add(field, glass, input, esc);
    var listing = el("div", "launcher-list");
    listing.id = "launcher-list";
    listing.setAttribute("role", "listbox");
    var hints = el("div", "launcher-hints");
    hints.innerHTML = "<span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span>";
    add(panel, field, listing, hints);
    add(root, scrim, panel);
    document.body.appendChild(root);

    function commands() {
      var all = [];
      sources.forEach(function (source) { (typeof source === "function" ? source() : [source]).forEach(function (command) { if (command) { all.push(command); } }); });
      return all;
    }
    /** How well a command answers what's typed: 0 for not at all, higher for a closer match. */
    function score(command, query) {
      if (!query) { return 1; }
      var hay = [command.title, command.detail || "", (command.keywords || []).join(" ")].join(" ").toLowerCase();
      var title = command.title.toLowerCase();
      var words = query.toLowerCase().split(/\s+/).filter(Boolean);
      var total = 0;
      for (var i = 0; i < words.length; i += 1) {
        var word = words[i];
        if (title.indexOf(word) === 0) { total += 4; }
        else if ((" " + title).indexOf(" " + word) >= 0) { total += 3; }
        else if (hay.indexOf(word) >= 0) { total += 1; }
        else { return 0; }
      }
      return total;
    }
    function draw() {
      var query = input.value.trim();
      shown = commands().map(function (command, order) { return { command: command, rank: score(command, query), order: order }; })
        .filter(function (entry) { return entry.rank > 0; })
        .sort(function (a, b) { return query ? b.rank - a.rank || a.order - b.order : a.order - b.order; })
        .map(function (entry) { return entry.command; });
      if (active >= shown.length) { active = Math.max(0, shown.length - 1); }
      listing.replaceChildren();
      if (!shown.length) { listing.appendChild(el("p", "launcher-none", "Nothing matches “" + query + "”.")); }
      var group = null;
      shown.forEach(function (command, index) {
        if (command.group !== group && !query) { group = command.group; listing.appendChild(el("div", "launcher-group", group)); }
        var row = el("div", "launcher-item" + (command.current && command.current() ? " is-here" : ""));
        row.id = "launcher-item-" + index;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(index === active));
        add(row, el("span", "launcher-lead", command.lead || ""), el("span", "launcher-title", command.title),
          command.detail ? el("span", "launcher-detail", command.detail) : null,
          command.current && command.current() ? el("span", "launcher-here", "You’re here") : null);
        row.addEventListener("pointermove", function () { if (active !== index) { active = index; mark(); } });
        row.addEventListener("click", function () { runAt(index); });
        listing.appendChild(row);
      });
      mark();
    }
    function mark() {
      Array.prototype.forEach.call(listing.querySelectorAll(".launcher-item"), function (row, index) {
        row.setAttribute("aria-selected", String(index === active));
        if (index === active && typeof row.scrollIntoView === "function") { row.scrollIntoView({ block: "nearest" }); }
      });
      input.setAttribute("aria-activedescendant", shown.length ? "launcher-item-" + active : "");
    }
    function runAt(index) {
      var command = shown[index];
      if (!command) { return; }
      close(false);
      command.run();
    }
    function open() {
      if (!root.hidden) { return; }
      before = document.activeElement;
      openTab(false);
      input.value = "";
      active = 0;
      var here = commands().findIndex(function (command) { return command.current && command.current(); });
      if (here >= 0) { active = here; }
      root.hidden = false;
      document.documentElement.classList.add("launcher-open");
      draw();
      input.focus();
    }
    function close(giveBack) {
      if (root.hidden) { return; }
      root.hidden = true;
      document.documentElement.classList.remove("launcher-open");
      if (giveBack !== false && before && typeof before.focus === "function") { before.focus(); }
    }
    input.addEventListener("input", function () { active = 0; draw(); });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (shown.length) { active = (active + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length; mark(); }
      } else if (event.key === "Enter") {
        event.preventDefault();
        runAt(active);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === "Tab") {
        event.preventDefault();
      }
    });
    scrim.addEventListener("click", function () { close(); });
    document.addEventListener("keydown", function (event) {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && (event.key === "k" || event.key === "K")) {
        event.preventDefault();
        if (root.hidden) { open(); } else { close(); }
        return;
      }
      var typing = event.target && (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable);
      if (event.key === "/" && !typing && root.hidden && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        open();
      }
    });
    return {
      add: function (source) { sources.push(source); },
      open: open,
      close: close,
      isOpen: function () { return !root.hidden; }
    };
  })();
  function launcherOpen() { return launcher.isOpen(); }

  // The pages, numbered as the chapter tab numbers them.
  launcher.add(function () {
    return chapterIds().map(function (id, index) {
      return { id: "page:" + id, group: "Pages", lead: String(index + 1).padStart(2, "0"), title: chapterName(id),
        keywords: [String(index + 1), id],
        current: function () { return id === currentPage; },
        run: function () { page.go({ chapter: id }, "turn"); } };
    });
  });
  if (historyHref) {
    launcher.add({ id: "history", group: "Elsewhere", lead: "↺", title: "Past deliveries and runs", keywords: ["history", "archive", "runs"],
      run: function () { location.href = historyHref; } });
  }

  // The corner's way in, for a reader who doesn't know the shortcut.
  var launchButton = el("button", "launch-button");
  launchButton.type = "button";
  var isMac = typeof navigator === "object" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
  launchButton.setAttribute("aria-label", "Go to… (" + (isMac ? "Command" : "Control") + " K)");
  launchButton.setAttribute("aria-keyshortcuts", isMac ? "Meta+K" : "Control+K");
  add(launchButton, el("span", "launch-label", "Go to"), el("kbd", "launch-key", (isMac ? "⌘" : "Ctrl ") + "K"));
  launchButton.addEventListener("click", function () { launcher.open(); });
  if (corner) { corner.insertBefore(launchButton, tab); }

  // Scrolling on past a page's end turns to the next page, and on past its top back to the one before, landing at its
  // end, so the pages still read as one scroll. A pull has to build past a threshold, counting starts only once the
  // reader has rested at the edge (a fling that reaches the end doesn't turn the page), and a turn is followed by a
  // short pause. A ring at that edge fills toward the page it would open.
  var PULL_WHEEL = 460, PULL_TOUCH = 170, REST_MS = 220, PAUSE_MS = 700;
  var pull = { dir: 0, amount: 0, idle: null, edgeSince: 0, pausedUntil: 0, touchY: null };
  var pullEl = el("div", "page-pull");
  pullEl.setAttribute("aria-hidden", "true");
  // The ring fills toward the turn around an arrow pointing the way; the words hold still, so the pill keeps its width.
  var pullRing = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  pullRing.setAttribute("class", "page-pull-ring");
  pullRing.setAttribute("viewBox", "0 0 20 20");
  [["circle", "track", { cx: 10, cy: 10, r: 8.25 }], ["circle", "fill", { cx: 10, cy: 10, r: 8.25, pathLength: 1 }],
    ["path", "arrow", { d: "M10 6.2v7.2M6.9 10.6 10 13.6l3.1-3" }]].forEach(function (part) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", part[0]);
    node.setAttribute("class", part[1]);
    Object.keys(part[2]).forEach(function (name) { node.setAttribute(name, String(part[2][name])); });
    pullRing.appendChild(node);
  });
  var pullLead = el("span", "page-pull-lead");
  var pullName = el("span", "page-pull-name");
  var pullWords = el("span", "page-pull-words");
  add(pullWords, pullLead, pullName);
  add(pullEl, pullRing, pullWords);
  document.body.appendChild(pullEl);
  function atEnd() { return window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2; }
  function atTop() { return window.scrollY <= 1; }
  function neighbour(dir) { var ids = chapterIds(); return ids[ids.indexOf(currentPage) + dir]; }
  function drawPull() {
    var to = pull.dir ? neighbour(pull.dir) : null;
    var share = to ? Math.min(1, pull.amount) : 0;
    pullEl.classList.toggle("is-on", share > 0.04);
    pullEl.classList.toggle("is-up", pull.dir < 0);
    pullEl.classList.toggle("is-full", share >= 1);
    pullEl.style.setProperty("--pull", String(share));
    if (to) { pullLead.textContent = pull.dir > 0 ? "Keep scrolling for" : "Keep scrolling up for"; pullName.textContent = chapterName(to); }
  }
  function resetPull() { pull.dir = 0; pull.amount = 0; drawPull(); }
  function turn(dir) {
    var to = neighbour(dir);
    resetPull();
    pull.pausedUntil = Date.now() + PAUSE_MS;
    pull.edgeSince = 0;
    if (!to) { return; }
    // Back up past a page's top lands at the previous page's end, so the pages still read as one scroll.
    page.go({ chapter: to }, dir < 0 ? "back-turn" : "turn");
  }
  function addPull(dir, amount, threshold) {
    if (pull.dir !== dir) { pull.dir = dir; pull.amount = 0; }
    pull.amount += amount / threshold;
    drawPull();
    clearTimeout(pull.idle);
    // A pull let go fades back rather than staying armed.
    pull.idle = setTimeout(resetPull, 260);
    if (pull.amount >= 1) { turn(dir); }
  }
  function edgeOf(dir) { return dir > 0 ? atEnd() : atTop(); }
  window.addEventListener("scroll", function () {
    if (!PAGED) { return; }
    if (atEnd() || atTop()) { if (!pull.edgeSince) { pull.edgeSince = Date.now(); } } else { pull.edgeSince = 0; if (pull.dir) { resetPull(); } }
  }, { passive: true });
  window.addEventListener("wheel", function (event) {
    if (!PAGED || event.ctrlKey || !tabList.hidden || launcherOpen() || Math.abs(event.deltaX) > Math.abs(event.deltaY)) { return; }
    var dir = event.deltaY > 0 ? 1 : event.deltaY < 0 ? -1 : 0;
    if (!dir || !edgeOf(dir) || !neighbour(dir) || Date.now() < pull.pausedUntil) { return; }
    if (!pull.edgeSince) { pull.edgeSince = Date.now(); }
    if (Date.now() - pull.edgeSince < REST_MS) { return; }
    var lines = event.deltaMode === 1 ? 32 : event.deltaMode === 2 ? window.innerHeight : 1;
    addPull(dir, Math.min(Math.abs(event.deltaY) * lines, 120), PULL_WHEEL);
  }, { passive: true });
  window.addEventListener("touchstart", function (event) {
    pull.touchY = event.touches.length === 1 ? event.touches[0].clientY : null;
    pull.touchEdge = { end: atEnd(), top: atTop() };
  }, { passive: true });
  window.addEventListener("touchmove", function (event) {
    if (!PAGED || pull.touchY === null || launcherOpen() || Date.now() < pull.pausedUntil) { return; }
    var moved = pull.touchY - event.touches[0].clientY;
    var dir = moved > 0 ? 1 : -1;
    if (!(dir > 0 ? pull.touchEdge.end && atEnd() : pull.touchEdge.top && atTop()) || !neighbour(dir)) { return; }
    pull.dir = dir;
    pull.amount = Math.min(1, Math.abs(moved) / PULL_TOUCH);
    drawPull();
  }, { passive: true });
  window.addEventListener("touchend", function () {
    if (pull.touchY !== null && pull.dir && pull.amount >= 1) { turn(pull.dir); } else if (pull.dir) { resetPull(); }
    pull.touchY = null;
  }, { passive: true });

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
      list(doc.state.followups).filter(function (entry) { return isObject(entry) && (entry.reference === id || entry.id === id); })[0] ||
      list(doc.state.outside).filter(function (entry) { return isObject(entry) && entry.subject === id; })[0] ||
      siblingStory(doc, id);
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
    var node = withKey(el(href ? "a" : "span", "issue-link", label === undefined ? id : label), "ticket", text(id));
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
  /**
   * When a rerun on the main line should end: its start plus each rerun check's last recorded passing time, from the
   * queue's history. Null when any of them has none, since a guess is not an estimate.
   */
  function rerunEndsAt(doc, rerun, checks) {
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var history = isObject(meta.checkHistory) && isObject(meta.checkHistory.checks) ? meta.checkHistory.checks : {};
    var start = seconds(rerun.startedAt);
    if (start === null || !checks.length) { return null; }
    var total = 0;
    for (var index = 0; index < checks.length; index += 1) {
      var times = isObject(history[checks[index]]) ? list(history[checks[index]].seconds) : [];
      var last = times.length ? times[times.length - 1] : null;
      if (typeof last !== "number" || !isFinite(last) || last < 0) { return null; }
      total += last;
    }
    return start + total;
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
  var RERUN_WORDS = "Failed · rerunning on the main line";
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

  /*
   * The integration step: a delivery whose record names an integration rejoins its target after its pieces land. Its
   * facts are the target merged in, the suite, the integration review's rounds under the delivery's own key and the
   * change request, each at a head; the newest head any of them names is the integration's current head. A suite, a
   * round or a request at another head is superseded; a merge only by a later merge. The landing ends it. A suite's
   * start runs until its finish or a later start; a start the suite then finished at the same head times that suite.
   */
  var INTEGRATION_FACTS = table({ "integration.merged": "merged", "integration.suite": "suite", "integration.requested": "requested",
    "integration.suite.started": "started" });
  var INTEGRATION_STOPS = ["merged", "suite", "round", "requested"];
  function integrationOf(doc) {
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var named = meta.integration;
    if (!isObject(named) || !text(named.target)) { return null; }
    var key = text(meta.trackerParent);
    var step = { base: text(named.base), target: text(named.target), head: text(meta.baseHead) || null, merges: [],
      latest: { merged: null, suite: null, round: null, requested: null, started: null }, landed: null, running: null, timed: null };
    list(doc.journal).forEach(function (entry) {
      if (!isObject(entry) || !isObject(entry.data)) { return; }
      if (entry.kind === "integration.landed") { step.landed = step.landed || entry; return; }
      var name = INTEGRATION_FACTS[entry.kind] || (entry.kind === "review.round" && key && entry.subject === key ? "round" : null);
      if (!name || step.landed) { return; }
      step.latest[name] = entry;
      if (name === "merged") { step.merges.push(entry); }
      if (name === "started") { step.running = step.timed = entry; }
      if (name === "suite") {
        step.running = null;
        if (step.timed && text(step.timed.data.head) !== text(entry.data.head)) { step.timed = null; }
      }
      if (text(entry.data.head)) { step.head = entry.data.head; }
    });
    return step;
  }
  // Whether a fact still stands: a merge until a later one, anything else while its head is the current head.
  function standing(step, name) {
    var entry = step.latest[name];
    if (!entry) { return false; }
    return name === "merged" || text(entry.data.head) === step.head;
  }
  // The suite running at the current head, from its start, or null.
  function suiteRunning(step) { return step.running && text(step.running.data.head) === step.head ? step.running : null; }
  // The start of the suite at the current head, running or finished since, or null: a timed suite draws its step wider.
  function suiteTimed(step) { return step.timed && text(step.timed.data.head) === step.head ? step.timed : null; }
  // How long a running suite has run and usually takes, in seconds, each null when the record can't say.
  function suiteClock(entry) {
    var from = seconds(entry.data.startedAt);
    if (from === null) { from = seconds(entry.at); }
    var usual = typeof entry.data.expectedSeconds === "number" && entry.data.expectedSeconds > 0 ? entry.data.expectedSeconds : null;
    return { spent: from === null ? null : Math.max(0, Date.now() / 1000 - from), usual: usual };
  }
  // "<n> min, about <m> usual", with ", so <k> over" once past the usual time.
  function suiteRunWords(entry) {
    var clock = suiteClock(entry);
    if (clock.spent === null) { return ""; }
    var spent = Math.floor(clock.spent / 60);
    if (clock.usual === null) { return spent + " min"; }
    var usual = Math.round(clock.usual / 60), over = clock.spent > clock.usual ? spent - usual : 0;
    return spent + " min, about " + usual + " usual" + (over >= 1 ? ", so " + over + " over" : "");
  }
  function suitePasses(entry) { return Boolean(entry) && (entry.data.result === "passed" || entry.data.result === "over-budget"); }
  function roundApproves(entry) {
    var verdicts = entry && isObject(entry.data.verdicts) ? entry.data.verdicts : {};
    var lenses = Object.keys(verdicts);
    return lenses.length > 0 && lenses.every(function (lens) { return verdicts[lens] === "approve"; });
  }
  // Each stop's state: "filled" when its standing fact passed, "danger" or "warning" when it stands and did not,
  // "running" for a suite running at the current head, "next" for the first of the rest while nothing runs, and
  // "hollow" otherwise. After the landing every stop is filled.
  function integrationStops(step) {
    var states = INTEGRATION_STOPS.map(function (name) {
      if (step.landed) { return "filled"; }
      if (name === "suite" && suiteRunning(step)) { return "running"; }
      if (!standing(step, name)) { return "hollow"; }
      var entry = step.latest[name];
      if (name === "suite") { return suitePasses(entry) ? "filled" : "danger"; }
      if (name === "round") { return roundApproves(entry) ? "filled" : "warning"; }
      return "filled";
    });
    var next = states.indexOf("running") < 0 ? states.indexOf("hollow") : -1;
    if (next >= 0) { states[next] = "next"; }
    return states;
  }
  function integrationFilled(step) { return integrationStops(step).filter(function (state) { return state === "filled"; }).length; }
  function plural(count, one, many) { return count + " " + (count === 1 ? one : many || one + "s"); }

  /* 1. Now */
  /**
   * Before anything lands: every piece as a stop in its lane's colour, in landing order, up to the finish post, each drawn
   * as the line draws it (not started, being built or reviewed, ready, waiting on the lead, being checked, stopped) and
   * said in the line's words. The row is ready to start only while nothing has. Its classes carry an is- prefix, so no
   * rule of the page's own for a bare state name reaches them.
   */
  function startingLine(doc, all) {
    var box = el("div", "starting");
    var stops = el("ol", "starting-stops");
    var current = checkingNow(doc);
    var nowSubject = current && current.subject !== undefined && current.subject !== null ? String(current.subject) : null;
    var started = false;
    all.forEach(function (piece, index) {
      var key = subjectOf(piece.item);
      var kind = stopKind(piece, nowSubject);
      if (kind !== "later") { started = true; }
      var words = piece.planned && piece.item.stage === "planned" ? STAGE_WORDS.planned : pieceWords(doc, piece.item);
      var stop = withKey(el("a", "starting-stop " + kind.split(" ").map(function (part) { return "is-" + part; }).join(" ")), "starting", key);
      stop.setAttribute("href", "#line");
      stop.style.setProperty("--c", laneColour(text(piece.item.lane)));
      stop.style.setProperty("--i", String(index));
      // Its name opens toward the room it has: rightward near the start, leftward near the finish.
      stop.classList.add(index < all.length / 3 ? "opens-right" : index >= all.length * 2 / 3 ? "opens-left" : "opens-mid");
      stop.setAttribute("aria-label", (key ? key + ", " : "") + titleOf(doc, piece.item) + ", " + words);
      stop.appendChild(add(el("span", "starting-name"), (key ? key + " · " : "") + titleOf(doc, piece.item), el("small", "starting-stage", words)));
      stops.appendChild(add(el("li"), stop));
    });
    add(box, el("div", "starting-end is-start", started ? "Under way" : "Ready to start"), stops,
      add(el("div", "starting-post"), el("span", "starting-post-mark")), el("div", "starting-end is-finish", "Finish line"));
    return box;
  }
  /*
   * Now's lead: one sentence naming the stage the delivery is at and who it waits on, up to four marks (done, live,
   * stop, todo) with the live one labelled by its holder, and at most one fact that changes what the reader would do.
   * Everything is read from the record, the supervisor's list and the forge's facts the renderer gave: nothing here
   * names a repository's own checks, branches or commands. An item that holds nothing the reader can act on, such as
   * a tracker behind the record or a target gaining commits the work already holds, never reaches it.
   */
  var MARK_RANK = { stop: 0, done: 1, live: 2, todo: 3 };
  var MARK_WORDS = { done: "done", live: "now", stop: "stopped", todo: "to come" };
  function nowMark(label, state, who) { return { label: label, state: state, who: who || null }; }
  /** A span of time in words: "12 minutes", "3 hours", "2 days". */
  function spanWords(total) {
    var minutes = Math.max(1, Math.round(total / 60)), hours = Math.floor(minutes / 60);
    if (minutes < 60) { return plural(minutes, "minute"); }
    return hours < 48 ? plural(hours, "hour") : plural(Math.floor(hours / 24), "day");
  }
  /** The story that is moving, for stage 1's second beat, with where it is: the one being checked, in review, queued or built. */
  var MOVING = [["is being checked", function (piece) { return !piece.planned && IN_CHECKS[piece.item.stage]; }],
    ["is in review", function (piece) { return piece.planned && piece.item.stage === "review"; }],
    ["is in the queue", function (piece) { return !piece.planned && piece.item.stage === "queued"; }],
    ["is being built", function (piece) { return piece.planned && piece.item.stage === "building"; }]];
  function moving(all) {
    for (var index = 0; index < MOVING.length; index += 1) {
      var found = all.filter(MOVING[index][1])[0];
      if (found) { return { piece: found, where: MOVING[index][0] }; }
    }
    return null;
  }
  /**
   * Stage 1's second beat, as the checks sheet below says it: the piece being checked, or the pieces checked together,
   * and how its run stands once it has an outcome. A run with a failed check won't merge (a batch won't land as one),
   * and a failure the queue is rerunning on the main line is said as that. Any other moving piece reads as it is.
   */
  function checkedWords(doc, all, next) {
    var item = next.piece.item, subject = subjectOf(item);
    if (next.where !== "is being checked") { return [" ", issue(subject), " " + next.where + "."]; }
    var current = checkingNow(doc);
    var batch = current ? batchNow(doc, all) : null;
    var steps = current ? list(current.steps).filter(isObject) : [];
    var failed = steps.some(function (step) { return step.state === "failed" || step.state === "timed_out"; });
    if (batch && batch.some(function (member) { return subjectOf(member) === subject; })) {
      var names = batch.length === 2 ? [issue(subjectOf(batch[0])), " and ", issue(subjectOf(batch[1]))] : [batch.length + " pieces"];
      return [" "].concat(names, [failed ? " won't land as one." : " are being checked together."]);
    }
    var own = current && current.subject !== undefined && current.subject !== null && String(current.subject) === subject;
    if (own && rerunOf(doc, item)) { return [" ", issue(subject), " failed a check, now being rerun on the main line."]; }
    if (own && failed) { return [" ", issue(subject), " won't merge from this run."]; }
    // Every check finished, none failed, and at least one passed or was reused: as the sheet's tick says, it has passed.
    var passed = steps.length > 0 && steps.every(function (step) { return DONE[step.state]; }) &&
      steps.some(function (step) { return step.state === "passed" || step.state === "reused"; });
    if (own && passed) { return [" ", issue(subject), " passed its checks and waits to land."]; }
    return [" ", issue(subject), " " + next.where + "."];
  }
  /** The row the lead named as the broken base's repair, while the base awaits one. */
  function repairOf(all) {
    return all.filter(function (piece) { return !piece.planned && isObject(piece.item.repair) && !isLanded(piece); })[0] || null;
  }
  function baseAwaitsRepair(doc) {
    var last = null;
    list(doc.journal).forEach(function (entry) { if (isObject(entry) && (entry.kind === "base.broken" || entry.kind === "base.repaired")) { last = entry.kind; } });
    return last === "base.broken";
  }
  /** Each lane as one mark: its landed stories of its own, done once all have landed, stopped while it holds a stopped story or the repair. */
  function laneMarks(doc, all, last) {
    var lanes = [], byLane = Object.create(null);
    all.forEach(function (piece) {
      var lane = text(piece.item.lane);
      if (!byLane[lane]) { byLane[lane] = []; lanes.push(lane); }
      byLane[lane].push(piece);
    });
    var repair = baseAwaitsRepair(doc) ? repairOf(all) : null;
    var marks = lanes.map(function (lane, at) {
      var own = byLane[lane], done = own.filter(isLanded).length;
      var name = lane ? laneLabel(lane) : noLane(doc);
      var made;
      if (repair && text(repair.item.lane) === lane) { made = nowMark(name + " · repairing", "stop"); }
      else if (own.some(function (piece) { return !piece.planned && piece.item.stage === "stopped"; })) { made = nowMark(name + " · stopped", "stop"); }
      else if (done === own.length) { made = nowMark(name + " · " + done + " of " + own.length, "done"); }
      else {
        var started = done > 0 || own.some(function (piece) { return !piece.planned || piece.item.stage !== "planned"; });
        // A delivery that closed with the lane's work unlanded has nothing at work any more.
        made = nowMark(name + " · " + done + " of " + own.length, started && !closedEntry(doc) ? "live" : "todo");
      }
      made.at = at;
      return made;
    });
    marks.sort(function (a, b) { return MARK_RANK[a.state] - MARK_RANK[b.state] || a.at - b.at; });
    // Past three lanes the rest share the third mark, in the state the most pressing of them is in.
    if (marks.length > 3) {
      var rest = marks.slice(2);
      var state = rest.map(function (made) { return made.state; }).sort(function (a, b) { return MARK_RANK[a] - MARK_RANK[b]; })[0];
      marks = marks.slice(0, 2).concat([nowMark(rest.length + " more lanes", state)]);
    }
    // The lanes run side by side, so their rows read in lane-number order; the state order above only chose which lanes
    // keep a row of their own, and the lanes sharing one come last.
    var folded = marks.filter(function (made) { return made.at === undefined; });
    marks = marks.filter(function (made) { return made.at !== undefined; }).sort(function (a, b) {
      return a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: "base" });
    }).concat(folded);
    // Each lane's mark says it is one, so the page can draw the lanes side by side feeding the finish that follows them.
    return marks.map(function (made) { var lane = nowMark(made.label, made.state); lane.lane = true; return lane; }).concat([nowMark(last, closedEntry(doc) && all.every(isLanded) && (last !== "Rejoin" || Boolean((integrationOf(doc) || {}).landed)) ? "done" : "todo")]);
  }
  /** The rejoin's four marks, the one at `at` live (or stopped) with its holder and its count, those before it done. */
  function rejoinMarks(at, state, who, count) {
    return ["Suite", "Review", "Merge", "Landed"].map(function (label, index) {
      if (index < at) { return nowMark(label, "done"); }
      if (index > at) { return nowMark(label, "todo"); }
      return nowMark(count ? label + " · " + count : label, state, who);
    });
  }
  /** The integration review round running at the integration's head: its latest start under the delivery's key that no round answers. */
  function rejoinRoundRunning(doc, step) {
    var key = text(isObject(doc.state.meta) ? doc.state.meta.trackerParent : "");
    if (!key) { return null; }
    var start = null;
    list(doc.journal).forEach(function (entry) {
      if (!isObject(entry) || entry.subject !== key || !isObject(entry.data)) { return; }
      if (entry.kind === "review.started") { start = entry; }
      else if (entry.kind === "review.round" && start && entry.data.round === start.data.round) { start = null; }
    });
    return start && typeof start.data.round === "number" && text(start.data.head) === step.head ? start : null;
  }
  /** The forge's word on a failing check of the change request: how many fail, and whether the target fails them too. */
  function failingFact(step) {
    var failing = isObject(FORGE) && isObject(FORGE.checks) ? list(FORGE.checks.failing).filter(isObject) : [];
    if (!failing.length) { return null; }
    var onTarget = failing.filter(function (check) { return check.onTarget === true; }).length;
    var said = failing.length === 1 ? "1 forge check fails" : failing.length + " forge checks fail";
    var target = step.target;
    if (onTarget === failing.length) { return said + ", on " + target + " too."; }
    if (!onTarget) { return said + ", and " + target + (failing.length === 1 ? " passes it." : " passes them."); }
    return said + ", " + onTarget + " of them on " + target + " too.";
  }
  function pendingChecks() {
    var checks = isObject(FORGE) && isObject(FORGE.checks) ? FORGE.checks : null;
    return checks && typeof checks.pending === "number" && checks.pending > 0 ? checks.pending : 0;
  }
  /** The follow-ups the close-out still has to file. */
  function unfiled(doc) {
    return list(doc.state.followups).filter(function (entry) { return isObject(entry) && (!text(entry.reference) || entry.reference === entry.id); }).length;
  }
  /** "All 9 stories are in.", or "The one story is in." */
  function allIn(count) { return count === 1 ? "The one story is in." : "All " + count + " stories are in."; }
  function closedDay(closed) {
    var at = seconds(eventTime(closed));
    if (at === null) { return ""; }
    var date = new Date(at * 1000);
    return MONTHS[date.getMonth()] + " " + date.getDate();
  }
  /** The stage the rejoin is at, once every story has landed: its sentence, marks and fact. */
  function rejoinStage(doc, all, step) {
    var allInNow = allIn(all.length) + " ";
    var request = step.latest.requested;
    // The change request by its number, linked, when the renderer gave its address; otherwise by what it is.
    var requestLink = function (starts) {
      var changeRequest = request ? text(request.data.changeRequest) : "";
      return changeRequest && changeTarget(changeRequest) ? changeLink(changeRequest) : starts ? "The change request" : "the change request";
    };
    var closed = closedEntry(doc);
    if (step.landed) {
      var where = [request ? requestLink(true) : "The work", " is in " + step.target + "."];
      if (closed) {
        var day = closedDay(closed);
        return { sentence: ["Delivered" + (day ? " " + day : "") + ": " + plural(all.length, "story", "stories") + " into " + step.target + "."],
          marks: rejoinMarks(4) };
      }
      var filing = unfiled(doc);
      return { sentence: where.concat([" The delivery is closing."]), marks: rejoinMarks(3, "live", null, "closing"),
        fact: filing ? { text: "Filing " + plural(filing, "follow-up") + "." } : null };
    }
    var later = function (entry) { return entry && request && entry.id > request.id; };
    // The target moved under the change request: the lead merged it in, and nothing has run at the new head yet.
    if (request && !standing(step, "requested") && later(step.latest.merged) && !suiteRunning(step)
        && !standing(step, "suite") && !standing(step, "round") && !rejoinRoundRunning(doc, step)) {
      return { sentence: [step.target + " moved. The lead is bringing ", requestLink(), " up to date."], marks: rejoinMarks(2, "live", "lead", "updating") };
    }
    if (standing(step, "requested")) {
      var fact = failingFact(step);
      fact = fact ? { text: fact } : null;
      if (pendingChecks()) { return { sentence: [allInNow, requestLink(true), "'s checks are still running."], marks: rejoinMarks(2, "live", "forge"), fact: fact }; }
      return { sentence: [allInNow, requestLink(true), " waits for " + rejoinWhose(doc) + " merge."], marks: rejoinMarksFor(doc, rejoinMarks(2, "live", "you")), fact: fact };
    }
    var running = rejoinRoundRunning(doc, step);
    if (running) {
      return { sentence: ["The integration review is in round " + running.data.round + "."], marks: rejoinMarks(1, "live", "lead", "round " + running.data.round) };
    }
    if (standing(step, "round")) {
      var round = step.latest.round;
      if (roundApproves(round)) { return { sentence: ["The review approved. The lead opens the change request next."], marks: rejoinMarks(2, "live", "lead") }; }
      var found = isObject(round.data.findings) ? round.data.findings : {};
      var fixes = [found.P0, found.P1].filter(function (n) { return typeof n === "number" && n > 0; }).reduce(function (a, b) { return a + b; }, 0);
      var number = typeof round.data.round === "number" ? round.data.round : 1;
      return { sentence: ["Round " + number + " asked for " + (fixes ? plural(fixes, "fix", "fixes") : "changes") + ". The lead is making " + (fixes === 1 ? "it." : "them.")],
        marks: rejoinMarks(1, "live", "lead", "fixing") };
    }
    var started = suiteRunning(step);
    if (started) {
      var clock = suiteClock(started);
      return { sentence: [allInNow + "The full suite is running" + (suiteRunWords(started) ? ": " + suiteRunWords(started) : "") + "."],
        marks: rejoinMarks(0, "live", "lead", clock.spent === null ? null : Math.floor(clock.spent / 60) + " min") };
    }
    if (standing(step, "suite")) {
      var suite = step.latest.suite.data;
      if (suite.result === "over-budget") {
        var spent = typeof suite.seconds === "number" ? suite.seconds : null, budget = typeof suite.budgetSeconds === "number" ? suite.budgetSeconds : null;
        var over = spent !== null && budget !== null ? Math.max(1, Math.round((spent - budget) / 60)) + " min over budget" : "over budget";
        return { sentence: ["Every test passed, " + over + ". It's " + rejoinWhose(doc) + " call to accept."], marks: rejoinMarksFor(doc, rejoinMarks(0, "live", "you")),
          fact: spent !== null && budget !== null ? { text: Math.round(spent / 60) + " min against a budget of " + Math.round(budget / 60) + " min." } : null };
      }
      if (suitePasses(step.latest.suite)) { return { sentence: ["The full suite passed. The integration review runs next."], marks: rejoinMarks(1, "live", "lead") }; }
      var happened = el("a", null, "What happened");
      happened.setAttribute("href", "#happened");
      return { sentence: ["The full suite failed at " + short(suite.head) + ". The lead is fixing it."], marks: rejoinMarks(0, "stop", "lead"),
        fact: { nodes: [happened, " has the run."], stop: true } };
    }
    return { sentence: [allInNow + "The full suite runs next."], marks: rejoinMarks(0, "live", "lead") };
  }
  /** Where the delivery stands: its stage, then the overrides a stopped queue, the reader's own call and an out-of-date page put over it. */
  function nowState(doc, all) {
    var closed = closedEntry(doc);
    // Every own story in while a sibling delivery's part is open or unread: the state tool's first sentence says this
    // repository's part is done, in place of the delivery's own close.
    var partDone = isObject(doc.across) ? list(doc.across.partDone).map(text).filter(Boolean) : [];
    var ownPart = partDone.length ? [partDone[0].replace(/\.$/, "") + "."] : null;
    var step = integrationOf(doc);
    var landed = all.filter(isLanded).length;
    var stage;
    if (step && all.length && (step.landed || (!closed && landed === all.length))) { stage = rejoinStage(doc, all, step); }
    else if (closed) {
      var tally = closedTally(all), day = closedDay(closed);
      var when = day ? " " + day : "";
      stage = { sentence: tally.total && tally.landed === tally.total && ownPart ? ownPart : [!tally.total ? "Closed" + when + " with " + (all.length ? "nothing left to land." : "nothing on the line.")
        : tally.landed === tally.total ? "Delivered" + when + ": " + plural(tally.landed, "story", "stories") + " landed."
        : "Closed" + when + ": " + tally.landed + " of " + plural(tally.total, "story", "stories") + " landed."],
        marks: all.length ? laneMarks(doc, all, step ? "Rejoin" : "Landed") : [] };
    } else if (!all.length) {
      stage = { sentence: ["Nothing is on the line yet."], marks: [] };
    } else if (landed === all.length) {
      stage = { sentence: ownPart || [allIn(all.length) + " The delivery is closing."],
        marks: laneMarks(doc, all, "Close").map(function (made, index, marks) { return index === marks.length - 1 ? nowMark("Close", "live", "lead") : made; }) };
    } else {
      var next = moving(all);
      // The count turns digit by digit as a live update moves it, as every count on the page does.
      stage = { sentence: [figures(el("span"), "now", landed + " of " + all.length), all.length === 1 ? " story is in." : " stories are in."].concat(next ? checkedWords(doc, all, next) : []),
        marks: laneMarks(doc, all, step ? "Rejoin" : "Landed") };
    }
    var now = { sentence: stage.sentence, stop: false, marks: stage.marks, fact: stage.fact || null };
    if (closed) { return now; }
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var liveMark = now.marks.filter(function (made) { return made.state === "live" || made.state === "stop"; })[0]
      || now.marks.filter(function (made) { return made.state === "todo"; })[0] || null;
    // A stopped queue: the sentence, in the stop colour, naming its repair; the rejoin's live mark turns the stop colour.
    var hold = isObject(meta.hold) ? meta.hold : null;
    if (hold || baseAwaitsRepair(doc)) {
      var repair = hold ? null : repairOf(all);
      now.sentence = hold ? ["The queue is paused" + (text(hold.reason) ? ": " + text(hold.reason).replace(/\.$/, "") : "") + "."]
        : ["The queue has stopped: the main line is broken. "].concat(repair ? [issue(subjectOf(repair.item)), " is the repair."] : ["No repair is named yet."]);
      now.stop = true;
      now.fact = { text: hold ? "Nothing merges until the hold lifts." : repair ? "Nothing merges until the repair lands." : "Nothing merges until a repair is named.", stop: true };
      if (step && liveMark && liveMark.state === "live" && landed === all.length) { liveMark.state = "stop"; }
    }
    // The reader's own call, from a current list: it takes the sentence, and the live mark becomes theirs.
    var current = NEEDS && NEEDS.running === true && seconds(NEEDS.staleAfter) !== null && Date.now() / 1000 <= seconds(NEEDS.staleAfter);
    var call = current ? list(NEEDS.items).filter(function (item) { return isObject(item) && yours(doc, item); })[0] : null;
    // Another seat's call takes nothing over: its mark names that seat, when no one is named on it yet.
    var other = current && !call ? list(NEEDS.items).filter(function (item) { return isObject(item) && theirs(doc, item); })[0] : null;
    if (other) {
      var asked = text(other.lane) ? laneLabel(text(other.lane)) : "";
      var marked = (asked ? now.marks.filter(function (made) { return made.label.split(" · ")[0] === asked; })[0] : null) || liveMark;
      if (marked && !marked.who) { marked.who = "seat"; marked.name = theirs(doc, other); }
    }
    if (call) {
      var said = needSaid(doc, call);
      var words = (text(said.headline) || text(call.text)).replace(/\s+$/, "");
      now.sentence = [/[.?!]$/.test(words) ? words : words + "."];
      now.stop = false;
      // The mark that becomes theirs: the asking lane's when the item names a lane drawn as a mark, else the live one.
      var asking = text(call.lane) ? laneLabel(text(call.lane)) : "";
      var mine = (asking ? now.marks.filter(function (made) { return made.label.split(" · ")[0] === asking; })[0] : null) || liveMark;
      if (mine) {
        mine.who = "you";
        delete mine.name;
        if (mine.state === "todo") { mine.state = "live"; }
        if (!(now.fact && now.fact.stop)) { now.fact = { text: mine.label.split(" · ")[0] + " waits on you." }; }
      }
    }
    // A page that may be out of date says so as its fact: a stopped or silent supervisor, or a record with no recent update.
    var stale = null;
    if (NEEDS && NEEDS.unreadable !== true && seconds(NEEDS.at) !== null) {
      var since = Math.max(0, Date.now() / 1000 - seconds(NEEDS.at));
      if (NEEDS.running !== true) { stale = "Updates stopped " + spanWords(since) + " ago · may be out of date"; }
      else if (seconds(NEEDS.staleAfter) === null || Date.now() / 1000 > seconds(NEEDS.staleAfter)) {
        stale = "Last update " + spanWords(since) + " ago · may be out of date";
      }
    }
    var updated = seconds(meta.updatedAt);
    if (!stale && updated !== null && Date.now() / 1000 - updated > 90 * 60) { stale = "No update for " + spanWords(Date.now() / 1000 - updated) + " · may be out of date"; }
    if (stale) { now.fact = { text: stale, stop: true }; }
    return now;
  }
  /** The lanes' threads into the finish: one curve per lane from its row to the middle, then the shared run to the
   * finish. A landed lane's thread is solid ink, a paused or stopped one dashed in the stop colour, the rest dashed. */
  function mergeThreads(lanes, finish, stop) {
    var ns = "http://www.w3.org/2000/svg", row = 24, height = lanes.length * row, mid = height / 2;
    var svg = document.createElementNS(ns, "svg");
    // A list holds only items, so the drawing is an item of its own, hidden from a screen reader, which hears the marks.
    var holder = el("li", "now-threads");
    holder.setAttribute("aria-hidden", "true");
    holder.style.gridRow = "1 / span " + lanes.length;
    svg.setAttribute("viewBox", "0 0 100 " + height);
    svg.setAttribute("preserveAspectRatio", "none");
    holder.appendChild(svg);
    var thread = function (d, kind) {
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "now-thread is-" + kind);
      path.setAttribute("vector-effect", "non-scaling-stroke");
      svg.appendChild(path);
    };
    lanes.forEach(function (made, at) {
      var y = at * row + row / 2;
      var kind = made.state === "done" ? "done" : made.state === "stop" || (stop && made.state === "live") ? "held" : "ahead";
      thread("M 0 " + y + " C 30 " + y + ", 30 " + mid + ", 60 " + mid, kind);
    });
    thread("M 60 " + mid + " L 100 " + mid, finish.state === "done" ? "done" : "ahead");
    return holder;
  }
  /** Now's lead as the page draws it. */
  function nowBlock(doc, all) {
    var now = nowState(doc, all);
    var box = el("div", "now-state");
    // A stop spends its colour on the words that name it, before the colon, on their own line; what follows reads in ink.
    var said = now.sentence, lead = now.stop && typeof said[0] === "string" ? /^([^:]{1,60}):\s*([\s\S]*)$/.exec(said[0]) : null;
    if (lead) { said = [el("span", "now-stop-lead", lead[1]), el("span", "sr", ": "), lead[2]].concat(said.slice(1)); }
    box.appendChild(add.apply(null, [el("h2", "now-sentence" + (now.stop ? " is-stop" : ""))].concat(said)));
    if (now.marks.length) {
      var marks = el("ol", "now-marks");
      marks.setAttribute("aria-label", "Where it stands");
      // Lanes run side by side, not one after another: while stories land, each lane is a row whose thread curves into
      // the one finish after them, which fills only once every lane is in.
      var finish = now.marks[now.marks.length - 1];
      var lanes = now.marks.slice(0, -1);
      var merge = lanes.length > 0 && !finish.lane && lanes.every(function (made) { return made.lane; });
      if (merge) { marks.classList.add("is-merge"); }
      now.marks.forEach(function (made) {
        // Under a stop, a live mark is paused where it stands: its ring says so, so the marks agree with the sentence.
        var paused = now.stop && made.state === "live";
        var li = el("li", "now-mark is-" + made.state + (paused ? " is-paused" : ""));
        li.setAttribute("data-state", made.state);
        if (made.who) { li.setAttribute("data-who", made.who); }
        var dot = el("span", "now-dot");
        dot.setAttribute("aria-hidden", "true");
        // A holder is named only when it changes what the reader does: you, or the forge holding it. The lead and the
        // lanes are the delivery moving under its own steam, which the live ring already says.
        var named = made.who === "you" || made.who === "forge" ? made.who : made.who === "seat" ? text(made.name) : "";
        add(li, dot, el("span", "now-mark-label", named ? made.label + " · " + named : made.label), el("span", "sr", ", " + (paused ? "paused" : MARK_WORDS[made.state])));
        if (merge && made === finish) { li.classList.add("is-finish"); li.style.gridRow = "1 / span " + lanes.length; }
        marks.appendChild(li);
      });
      if (merge) {
        marks.insertBefore(mergeThreads(lanes, finish, now.stop), marks.lastChild);
        // Where the threads meet, so the sentence beside them starts on the finish's line and reads on from it.
        box.style.setProperty("--merge-mid", lanes.length * 12 + "px");
      }
      box.appendChild(marks);
    }
    if (now.fact) { box.appendChild(add.apply(null, [el("p", "now-fact" + (now.fact.stop ? " is-stop" : ""))].concat(now.fact.nodes || [now.fact.text]))); }
    return box;
  }
  function drawNow(doc, all) {
    // A redraw can remove what the card describes without a mouseleave; such a card hides.
    if (!card.hidden && cardTarget && !cardTarget.isConnected) { hideCard(); }
    var inner = parts.now;
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var landed = all.filter(isLanded).length;
    var closed = closedEntry(doc);
    var step = closed ? null : integrationOf(doc);
    var tally = closed ? closedTally(all) : null;
    // What sibling deliveries still hold once this repository's part is done, as the state tool says it: under Now.
    var partDone = isObject(doc.across) ? list(doc.across.partDone).map(text).filter(Boolean) : [];
    var behind = partDone.length > 1 && landed === all.length && !(step && !closed) ? partDone.slice(1).join(" ") : null;
    var outcome = plan && isObject(plan.outcome) ? text(plan.outcome.summary) : "";
    var parent = text(meta.trackerParent);
    // The delivery's name is the headline. A ticket key it opens with is left off when the kicker links that ticket, or
    // links none, and what remains starts with a capital.
    var name = text(meta.headline) || outcome || pageTitle();
    var keyed = name.match(/^([A-Z][A-Z0-9]*-\d+):\s*(.+)$/);
    if (keyed && (!parent || keyed[1] === parent)) { name = keyed[2].charAt(0).toUpperCase() + keyed[2].slice(1); }
    // A closing parenthetical on the name ("(draft, as of d3db458b6)") is its status, not its name: it moves to the kicker.
    var status = /^(.*\S)\s*\(([^()]{1,80})\)$/.exec(name);
    if (status) { name = status[1]; }
    var title = add(el("h1", "title"), name);
    // A name past three lines is cut with an ellipsis: pointing at it shows it whole on the page's own card.
    title.addEventListener("mouseenter", function () {
      if (title.scrollHeight <= title.clientHeight + 1) { return; }
      card.replaceChildren(el("div", "title-whole", name));
      placeCard(title);
    });
    title.addEventListener("mouseleave", hideCard);
    // The kicker is one line: the repository the work lands in, the tracker item it is listed under, linked when the
    // renderer linked it, then how long it has run.
    var kicker = el("div", "kicker");
    // The repository the work lands in leads, as the configuration names it, so a page among several reads as this one's
    // first; then the tracker item.
    if (text(VIEW.repository)) { add(kicker, el("span", "kicker-repo", text(VIEW.repository))); }
    if (parent) { add(kicker, kicker.firstChild ? " · " : null, issue(parent)); }
    if (status) { add(kicker, kicker.firstChild ? " · " : null, el("span", "now-status", status[2])); }
    // How long the delivery has run, from the record's first entry: to now while it delivers, to its close once delivered.
    // The start, and for a closed one the end, read on pointing at it, in local time with UTC beside it.
    var first = list(doc.journal).filter(isObject)[0];
    var begun = first && first.id === 1 ? seconds(first.at) : null;
    var ended = closed ? seconds(eventTime(closed)) : Date.now() / 1000;
    if (begun !== null && ended !== null && ended >= begun) {
      var minutes = Math.round((ended - begun) / 60), hours = Math.floor(minutes / 60);
      var lasted = minutes < 60 ? minutes + " min" : hours < 48 ? hours + " h" + (minutes % 60 ? " " + minutes % 60 + " min" : "") : Math.round(hours / 24) + " days";
      var time = figures(el("time", "kicker-time"), "kicker", closed ? "Closed after " + lasted : "Started " + lasted + " ago");
      time.setAttribute("datetime", text(first.at));
      time.setAttribute("tabindex", "0");
      withKey(time, "time", "kicker");
      // The page's own card says the ends, as a ticket's does, since nothing on the page carries a native tooltip: a
      // small line from the start to the close, each end as a person says it, and the run between.
      var showEnds = function () {
        var when = el("div", closed ? "when" : "when open");
        var end = function (word, at, kind) {
          add(when, el("span", "when-dot " + kind), add(el("div", "when-end"), el("div", "when-word", word),
            el("div", "when-local", spokenMoment(at))));
        };
        end("Started", begun, "start");
        if (closed) {
          add(when, el("span"), el("div", "when-took", spokenDuration(ended - begun)));
          end("Closed", ended, "close");
        } else {
          add(when, el("span"), el("div", "when-took", spokenDuration(Date.now() / 1000 - begun) + " so far"));
        }
        card.replaceChildren(when);
        placeCard(time);
      };
      time.addEventListener("mouseenter", showEnds);
      time.addEventListener("focus", showEnds);
      time.addEventListener("mouseleave", hideCard);
      time.addEventListener("blur", hideCard);
      add(kicker, kicker.firstChild ? " · " : null, time);
    }
    // Before anything has landed, the page looks ahead: what is about to be delivered, rather than how much of it is zero.
    var fresh = !closed && all.length > 0 && landed === 0 && !step && !partDone.length;
    inner.parentNode.classList.toggle("is-fresh", fresh);
    // The delivery's name is the headline; under it, Now's status row: one sentence, its marks and at most one fact.
    var nowNode = nowBlock(doc, all);
    var seats = seatLine(doc);
    var nodes = (kicker.firstChild ? [kicker, title] : [title]).concat(seats ? [seats] : [], [nowNode]);
    // The outcome when it says more than the name, then how much of the plan is proven and how often it changed,
    // linked to the plan chapter.
    var said = outcome && outcome !== text(meta.headline) && outcome !== name ? outcome : null;
    var planLink = planNow(doc);
    if (said || planLink) {
      nodes.push(add(el("p", "plan-now"), said, planLink ? add(el("span", "plan-now-line"), planLink) : null));
    }
    // Before anything lands, the starting line stands where the journey will: every piece, drawn where it stands.
    if (fresh) { nodes.push(startingLine(doc, all)); }
    if (behind) { nodes.push(el("p", "say say-across", behind)); }
    // A lead's own status line, while it stands, is theirs to say.
    var sayWords = fresh ? "" : sayLine(doc);
    if (sayWords) { nodes.push(el("p", "say", sayWords)); }
    // What the lead waits on in another delivery sharing the base, and what waits on the lead, as the state tool says.
    waitLines(doc, "lead", "p", "say say-wait").forEach(function (line) { nodes.push(line); });
    var panel = closed ? null : checkingPanel(doc, all);
    // While a piece is checked, the checks lead: their sheet stands right under the sentence that names the piece, as
    // wide as the rows beneath it. Otherwise it keeps its place below.
    var leading = panel && panel.classList.contains("live");
    // Under a stop nothing is checked, so a sheet of pieces waiting their turn steps back to a quiet well.
    if (panel && !leading && nowNode.querySelector(".now-sentence.is-stop")) { panel.classList.add("is-held"); }
    if (leading) { panel.classList.add("is-leading"); nodes.splice(nodes.indexOf(nowNode) + 1, 0, panel); }
    else if (panel) { nodes.push(panel); }
    var supervisor = needsYou(doc, all, Boolean(closed));
    // "Needs you" is a sheet of its own, as wide as the rows. When something on it waits on the reader, it leads Now,
    // right under the sentence, ahead of any running checks: that is what the reader is here to do.
    var ownCall = supervisor && Boolean(supervisor.querySelector(".needs-row[data-yours]"));
    if (supervisor) { supervisor.classList.add("is-sheet"); }
    if (ownCall) { supervisor.classList.add("is-leading"); nodes.splice(nodes.indexOf(nowNode) + 1, 0, supervisor); }
    else if (supervisor) { nodes.push(supervisor); }
    var left = closed ? leftOpen(doc, all) : null;
    if (left) { nodes.push(left); }
    var partway = closeStoppedPartway(doc, closed);
    if (partway) { nodes.push(partway); }
    if (!fresh) { nodes.push(journey(doc, all, closed ? tally.landed : landed)); }
    // Under the line, three facts in one shape: how many stories have landed, what landed last, or before anything has
    // what the queue lands next, and the record's state.
    nodes.push(add(el("div", "facts"), storiesFact(all, closed ? tally : null),
      // While the checking sheet shows, it already says what lands next.
      fresh ? (panel ? null : nextToLand(doc, all)) : lastLanded(doc, all),
      since(doc, meta, closed)));
    var was = prev.checkingHeight;
    inner.replaceChildren.apply(inner, nodes);
    // A live update that changes the sheet's height, a check starting or a failure listed, eases it there instead of jumping.
    prev.checkingHeight = panel ? panel.offsetHeight : null;
    if (panel && was && was !== prev.checkingHeight) { easeSheet(panel, was); }
  }

  /** A lead's override of the status line, shown whole while it stands; the derived status is Now's sentence. */
  function sayLine(doc) {
    var status = isObject(doc.status) ? doc.status : {};
    return status.source === "override" ? text(status.text) : "";
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
    var value = add(el("span", "fact-count"), figures(el("span"), "stories", String(landed)), figures(el("span", "fact-of"), "stories-of", " of " + total + " landed"));
    var track = el("span", "fact-track");
    track.setAttribute("aria-hidden", "true");
    [["landed", landed], ["queued", queued], ["building", building]].forEach(function (part) {
      if (!part[1] || !total) { return; }
      var span = el("i", "is-" + part[0]);
      span.style.width = (part[1] / total * 100) + "%";
      track.appendChild(span);
    });
    var said = [queued ? queued + " in the queue" : "", building ? building + " being built" : ""].filter(Boolean).join(" · ");
    return fact("stories", "Stories", value, add(el("span", "fact-progress"), track, said ? figures(el("span", "fact-said"), "stories-said", said) : null));
  }
  /** A pull request as a fact's quiet reference: its number, linked to the forge when the renderer gave its address. */
  function pullRef(changeRequest) {
    var target = changeTarget(changeRequest);
    if (!target) { return el("span", "fact-ref", text(changeRequest)); }
    var link = withKey(el("a", "fact-ref", target.label), "request", text(changeRequest));
    link.setAttribute("href", target.href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    link.setAttribute("aria-label", "Open pull request " + (typeof target.repository === "string" && target.repository ? target.repository + " " : "") + target.label);
    return link;
  }
  /** The piece that landed last, by its title, with its ticket, its pull request when the forge snapshot knows it, and when. */
  /** Before anything lands, in place of the last landed: the piece the queue lands next, the one being checked first. */
  function nextToLand(doc, all) {
    var current = checkingNow(doc);
    var nowSubject = current && current.subject !== undefined && current.subject !== null ? String(current.subject) : null;
    var rank = { now: 0, ready: 1, "ready pending": 2 };
    var queued = all.filter(function (piece) { return !piece.planned && rank[stopKind(piece, nowSubject)] !== undefined; })
      .map(function (piece, index) { return { piece: piece, index: index, rank: rank[stopKind(piece, nowSubject)] }; })
      .sort(function (a, b) { return a.rank - b.rank || a.index - b.index; });
    if (!queued.length) { return fact("", "Next to land", "Nothing in the queue yet"); }
    var item = queued[0].piece.item;
    var ticket = subjectOf(item) ? issue(subjectOf(item)) : null;
    if (ticket) { ticket.classList.add("fact-ref"); }
    return fact("", "Next to land", titleOf(doc, item), add(el("span"), ticket, ticket ? " \u00b7 " : null, pieceWords(doc, item)));
  }
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
    box.setAttribute("data-region", "checking");
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
      // One line under the title says it all: which checks failed, that the main line is rerunning them to tell whose
      // failure it is, and when that should be known, by the failed checks' own history, or else since when it ran.
      // How the lead may end it sooner is the lead's to know, not the reader's.
      var rerunEnd = rerunEndsAt(doc, current.baseRerun, rerunChecks);
      var whenWords = rerunSince ? " · started " + rerunSince : "";
      if (rerunEnd !== null) {
        var left = rerunEnd - Date.now() / 1000;
        whenWords = left >= 60 ? " · about " + spokenDuration(left) + " left"
          : left > -60 ? " · should end about now" : " · should have ended " + spokenDuration(-left) + " ago";
      }
      add(box, el("div", "ck-rerun", (rerunChecks.length ? spokenList(rerunChecks) : "A check") + " failed. The main line is " +
        "rerunning " + (rerunChecks.length > 1 ? "them" : "it") + " to tell whose failure it is" + whenWords + "."));
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
    // The lead's note is built once the sheet has said whether it lists the run's checks itself: they are listed once, so
    // the note opens to the list only when the sheet draws the run line, or nothing, in its place.
    function leadNotes(listedHere) {
      var chosenItems = (batch || (byId[current.subject] ? [byId[current.subject]] : [])).filter(function (item) {
        return isObject(item.checkSelection) && item.checkSelection.by === "lead";
      });
      var listNodes = chosenItems.length && !listedHere ? checkListNodes(steps) : [];
      var withList = function (first) {
        var body = el("div", "ck-rest");
        [first].concat(listNodes).forEach(function (node) { if (node) { body.appendChild(node); } });
        return body;
      };
      if (batch && chosenItems.length) {
        var reasons = el("ul", "ck-chosen-list");
        chosenItems.forEach(function (item) {
          var reason = text(item.checkSelection.reason);
          add(reasons, add(el("li"), add(el("b"), titleOf(doc, item)), reason || null));
        });
        chosenNotes.push(chosenFold("batch:" + chosenItems.map(subjectOf).join(","), "Checks chosen by the lead, not the usual selection · for " +
          (chosenItems.length === batch.length ? (batch.length === 2 ? "both pieces" : "all " + batch.length + " pieces") : chosenItems.length + " of " + batch.length + " pieces"),
          listNodes.length ? withList(reasons) : reasons));
      } else {
        chosenItems.forEach(function (item) {
          var reason = text(item.checkSelection.reason);
          var words = "Checks chosen by the lead, not the usual selection" + (reason ? ": " + reason : "");
          // A fold opens to something more than its own line, or it is a line: the reason is read whole either way.
          chosenNotes.push(listNodes.length ? chosenFold(subjectOf(item), words, withList(null)) : el("p", "ck-chosen-line", words));
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
    }
    // The run's checks by name, one list ordered by state, ten to a page: the sheet's own list, and the lead's note's when
    // the sheet draws the run line instead. Nothing when no check waits or is done.
    function checkListNodes(steps) {
      // What waits its turn, then what passed, was reused or was skipped.
      var STATE_ORDER = { pending: 0, passed: 1, reused: 2, skipped: 3 };
      var rank = function (step) { var state = STEP_WORDS[step.state] ? step.state : "pending"; return state in STATE_ORDER ? STATE_ORDER[state] : 0; };
      var listed = steps.filter(function (step) { return step.state !== "running"; }).sort(function (a, b) {
        return rank(a) - rank(b) || steps.indexOf(a) - steps.indexOf(b);
      });
      var checkList = el("ol", "ck-list");
      var checkRow = function (step) {
        // The mark draws the state and the order groups it, so no row repeats it in words; a screen reader hears it
        // before the name. Only a reused result says so after its name: its tick alone would read as a fresh pass.
        var state = STEP_WORDS[step.state] ? step.state : "pending";
        var row = add(el("li", "ck-step " + state), el("span", "sr", STEP_WORDS[state] + ": "), stepName(step),
          state === "reused" ? el("span", "ck-item-note", " · reused") : null);
        // The note repeats what a screen reader already heard before the name.
        var note = row.querySelector(".ck-item-note");
        if (note) { note.setAttribute("aria-hidden", "true"); }
        // A title cut short is there whole to read on pointing at it.
        if (text(step.title).length > TITLE_SHOWN) { row.setAttribute("title", text(step.title)); }
        return row;
      };
      // Ten checks to a page, so a long run's list reads in steps rather than a column down the page. The page read
      // stays across live redraws of the same run, moved back only when the list has grown too short for it.
      var PAGE_SIZE = 10;
      var pageCount = Math.ceil(listed.length / PAGE_SIZE);
      var pageKey = text(current.startedAt) + "|" + text(current.subject);
      var pageRead = checksRead.get();
      var checksPage = isObject(pageRead) && pageRead.run === pageKey ? Math.min(pageRead.page, Math.max(0, pageCount - 1)) : 0;
      var pager = pageCount > 1 ? el("div", "ck-pager") : null;
      var showChecks = function () {
        checksRead.set({ run: pageKey, page: checksPage });
        checkList.replaceChildren.apply(checkList, listed.slice(checksPage * PAGE_SIZE, (checksPage + 1) * PAGE_SIZE).map(checkRow));
        checkList.setAttribute("start", String(checksPage * PAGE_SIZE + 1));
        if (!pager) { return; }
        var from = checksPage * PAGE_SIZE + 1, to = Math.min(listed.length, (checksPage + 1) * PAGE_SIZE);
        var step = function (by, words, mark) {
          var button = withKey(add(el("button", "ck-page-step"), mark), "pager", by < 0 ? "previous" : "next");
          button.type = "button";
          button.setAttribute("aria-label", words);
          button.disabled = checksPage + by < 0 || checksPage + by >= pageCount;
          button.addEventListener("click", function () { checksPage += by; page.redraw(showChecks, "reader"); });
          return button;
        };
        var where = el("span", "ck-page-where", from + "–" + to + " of " + listed.length);
        where.setAttribute("aria-live", "polite");
        pager.replaceChildren(step(-1, "Previous checks", "‹"), where, step(1, "Next checks", "›"));
      };
      showChecks();
      return listed.length ? [checkList].concat(pager ? [pager] : []) : [];
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
        // The outcome is said once, in the header beside its mark: the piece won't merge from this run, or a batch won't
        // land as one. A rerun on the main line keeps its own words, which already say the run failed. The counts are the
        // sheet's last line, so they aren't said here too; a batch adds only what its header can't: what can still land.
        var outcome = head.querySelector(".ck-h-words");
        if (batch) { outcome.textContent = "Won't land as one · " + batch.length + " pieces"; }
        else if (!waitsOnRerun && outcome.firstChild && outcome.firstChild.nodeType === 3) { outcome.firstChild.data = "Won't merge from this run"; }
        if (batch) { add(box, el("div", "ck-verdict", "The pieces ahead of the one that broke it can still land.")); }
      }
      var line = null;
      // Each part of the sheet says one thing: the header's count how far along the run is, the running rows what is
      // happening now, and the fold what is left, by name. A run line joins them only where the record times the checks
      // of a long or failed run, since it alone shows when each ran and what ran alongside it. Untimed, nothing stands in
      // for it: a strip of equal stretches would only say the count again.
      var groups = null;
      line = steps.length > 12 || failed ? runLine(current, steps) : null;
      if (line) { add(box, line); }
      else if (!failed) { groups = checkListNodes(steps); }
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
    leadNotes(Boolean(groups && groups.length));
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
  function runLine(current, steps) {
    var start = seconds(current.startedAt);
    var now = Date.now() / 1000;
    var timed = steps.filter(function (step) { return seconds(step.startedAt) !== null; });
    // Every check that ran needs a time to place it: with only some timed, the line would show those few crowded at now
    // and leave the rest out, so no line is drawn and the sheet's count and list say it instead. A reused or skipped check
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
   * supervisor running a current pass to finish it. It says how the lead finishes it, in a person's words: by resuming
   * the close when the record meets the close condition, and otherwise with a close that doesn't wait for the tracker,
   * since a resumed one would hold on what is unmet. A record with no marks, or marks for an earlier close, closed as
   * before.
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
      ? "The lead finishes it by resuming the close."
      : "The lead finishes it with a close that doesn't wait for the tracker, since a resumed close would hold on what is still open.")));
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
    // A list the page can't show is a notice, not a sheet of work: the box says so, and the page draws it as one line.
    // Its words say once that the list is hidden and why, and who brings it back: the lead, never a command to type.
    function unavailable() {
      box.classList.add("is-unavailable");
      return add(box, add.apply(null, [el("p", "needs-none")].concat([].slice.call(arguments))));
    }
    if (NEEDS.unreadable === true) { return unavailable("Hidden: this list can't be read."); }
    if (NEEDS.running !== true) {
      return unavailable("Hidden while updates are stopped, until the lead restarts them.");
    }
    var stale = seconds(NEEDS.staleAfter);
    if (stale === null || Date.now() / 1000 > stale) {
      return unavailable("Hidden: the list is from " + (ago(NEEDS.at) || "an unknown time") + ", more than a pass old. Updates may have stopped.");
    }
    // Every word of a row comes from the list, which the supervisor said from the table of wait sentences; the page
    // only lays it out. Holding rows lead, and within each, the rows the reader acts on.
    var items = list(NEEDS.items).filter(isObject).map(function (item, at) { return { item: item, at: at }; });
    var rank = function (entry) { return (entry.item.holding === true ? 0 : 2) + (yours(doc, entry.item) ? 0 : 1); };
    items.sort(function (a, b) { return rank(a) - rank(b) || a.at - b.at; });
    // The pass's sentence for its own seat; a page with no seat reads the one that names every owner.
    var summary = collabOf(doc) && !viewerOf(doc) ? text(NEEDS.summaryAll) : text(NEEDS.summary);
    if (summary) { box.appendChild(el("p", "needs-summary", summary)); }
    if (!items.length) { return box; }
    var ul = el("ul", "needs-rows");
    items.forEach(function (entry) {
      var item = entry.item, said = needSaid(doc, item);
      var li = el("li", "needs-row " + (item.holding === true ? "is-holding" : "is-quiet") + (theirs(doc, item) ? " is-theirs" : ""));
      li.setAttribute("data-who", text(item.who));
      if (yours(doc, item)) { li.setAttribute("data-yours", ""); }
      var margin = needsMargin(doc, all, item);
      var meta = add(el("div", "nr-meta"), el("span", "nr-who", text(said.who)));
      if (item.holding === true) { add(meta, add(el("span", "nr-holding"), el("span", "nr-dot"), text(words.holding))); }
      if (text(said.clears)) { add(meta, el("span", "nr-clears", text(said.clears))); }
      var body = add(el("div", "nr-body"), el("div", "a1", text(said.headline) || text(item.text)),
        text(said.why) ? el("div", "a2", text(said.why)) : null, meta);
      var age = el("span", "nr-age");
      var since = seconds(item.since);
      if (since !== null && Date.now() / 1000 >= since && text(words.age)) {
        figures(age, "need:" + [item.kind, item.who, item.lane, item.subject].map(text).join("/"), text(words.age).replace("{age}", duration(Date.now() / 1000 - since)));
        age.setAttribute("title", clock(item.since));
      }
      ul.appendChild(add(li, margin, body, age));
    });
    box.appendChild(ul);
    if (typeof NEEDS.more === "number" && NEEDS.more > 0) { box.appendChild(figures(el("p", "needs-more"), "needs-more", NEEDS.more + " more not shown.")); }
    return box;
  }

  function journey(doc, all, landed) {
    var finish = list(doc.state.finish).filter(isObject);
    var met = finish.filter(function (line) { return line.met === true; }).length;
    var meant = closedEntry(doc) ? closedTally(all).total : all.length;
    // An integration's four stops are the journey's last: the bar is full only once the work is on the target.
    var step = closedEntry(doc) ? null : integrationOf(doc);
    var stopsMeant = step ? INTEGRATION_STOPS.length : 0;
    var total = meant + finish.length + stopsMeant;
    var progress = total ? (landed + met + (step ? integrationFilled(step) : 0)) / total : 0;
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
    var stops = step
      ? [["Start", 0, "start"], ["Pieces landed", meant / total, "pieces"]].concat(
        finish.length ? [["Finish line met", (meant + finish.length) / total, "pieces"]] : [], [["Rejoined " + step.target, 1, "end"]])
      : finish.length
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
        var sha = withKey(el(href ? "a" : "span", href ? "issue-link head" : "head", short(meta.baseHead)), "commit", meta.baseHead);
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
  var LINE_STOP_Y = 23, LINE_PITCH = 84;
  // A lane row is one width whatever its story count: its landed stories fold into one segment, at most this many open
  // ones keep a stop, and the rest fold into "+n". The stylesheet gives each of its slots an equal share.
  var OPEN_STOPS = 3, ROW_SLOTS = OPEN_STOPS + 2;
  // Past the join the main line carries one tick per story, this far apart at most and at least, with a wider gap between
  // each group of five; the run of ticks aims at this length.
  var JOIN_X = 100, TICKS_FROM = 22, TICK_MOST = 15, TICK_LEAST = 8, TICK_SPAN = 190, TICK_GROUP = 5, GROUP_GAP = 5;
  // With an integration step a bar of its four steps follows the ticks, and the line bends into the target at the finish
  // post, drawn this far beneath it.
  var BAR_GAP = 34, BAR_WIDTH = 140, BAR_TO_POST = 56, TARGET_DROP = 34, TARGET_RUN = 64;
  // A timed suite's step takes about 44% of the bar, as the study draws it; each other step takes this share.
  var TIMED_SHARE = 44 / 236;
  /**
   * A timed suite's step, over its rect. Running: a stripe that moves (still under reduced motion), filling the step as
   * the run nears its usual time, with a tick at that time; past it the stripe fills the step and the tick moves back
   * to where the usual time falls. Over its budget: a cap at the step's end. Passed and failed draw as their rect does.
   */
  function timedSuite(svg, step, state, x, trunkY, width) {
    var y = trunkY - 5;
    if (state === "running") {
      var clock = suiteClock(step.running);
      var share = clock.usual !== null && clock.spent !== null ? clock.spent / clock.usual : null;
      var fill = share === null ? width : width * Math.min(1, share);
      var clip = shape(svg, "clipPath", { id: "mg-suite-clip" });
      shape(clip, "rect", { x: x, y: y, width: fill, height: 10, rx: 2 });
      var run = shape(svg, "g", { class: "mg-suite-run", "clip-path": "url(#mg-suite-clip)", "aria-hidden": "true" });
      var stripes = shape(run, "g", { class: "mg-stripes" });
      shape(stripes, "rect", { class: "mg-stripe-ground", x: x - 8, y: y, width: width + 16, height: 10 });
      var d = "";
      for (var at = x - 16; at < x + width + 8; at += 8) { d += "M" + at + " " + (y + 10) + "l10 -10h4l-10 10z"; }
      shape(stripes, "path", { class: "mg-stripe", d: d });
      if (clock.usual !== null && clock.spent !== null) {
        var tickX = x + width * (share > 1 ? 1 / share : 1);
        shape(svg, "path", { class: "mg-suite-usual", d: "M" + tickX + " " + (y - 3) + "V" + (y + 13), "aria-hidden": "true" });
      }
    } else if (state === "filled" && step.latest.suite && step.latest.suite.data.result === "over-budget") {
      shape(svg, "rect", { class: "mg-suite-over", x: x + width - Math.max(4, width * 10 / 104), y: y, width: Math.max(4, width * 10 / 104),
        height: 10, "aria-hidden": "true" });
    }
  }
  // Without one the post stands this far past the last tick. With sibling repositories drawn, the epic's line runs on past
  // the post to a join where the siblings turn in, and a post of its own.
  var POST_GAP = 40, EPIC_JOIN_GAP = 66, EPIC_POST_GAP = 128, MERGE_TOP = 16, MOST_DRAWN = 2;
  // Half of "Finish line" at the label's 13px, with a little room; the tally's 11px mono advances 0.6em a character.
  var LABEL_HALF = 36, TALLY_CHAR = 6.6;
  // The merge's texts' sizes, as the stylesheet sets them, so the room above the line holds every line of the epic's words.
  var TEXT_SIZE = { "mg-label": 13, "mg-count": 11, "mg-step-label": 10, "mg-why": 10 };
  /**
   * The epic's siblings as the line draws them, or null when no group is of this epic: then the line is drawn as it
   * always was. Only same-epic groups count anywhere here; the state tool decides which those are. The siblings this
   * repository waits on come first, in the record's order; the rest follow furthest behind first, by the lowest share of
   * stories landed, then the most open, then the record's order. A fresh sibling with no stories has nothing to be
   * behind on and comes last; one that can't be read and lists none counts as nothing landed. The first two are drawn.
   */
  function epicOf(doc, all) {
    var groups = isObject(doc.across) ? list(doc.across.groups).filter(function (group) {
      return isObject(group) && text(group.name) && group.lastOutcome !== "other-epic";
    }) : [];
    if (!groups.length) { return null; }
    var waited = list(doc.waits).filter(function (wait) { return isObject(wait) && wait.cause === "sibling" && typeof wait.on === "string"; })
      .map(function (wait) { return wait.on; });
    var shown = groups.map(function (group, index) {
      var stories = list(group.stories).filter(function (story) { return isObject(story) && text(story.subject); });
      var landed = stories.filter(function (story) { return story.landed === true; }).length;
      return { group: group, name: text(group.name), index: index, fresh: group.fresh === true, total: stories.length, landed: landed,
        waited: stories.some(function (story) { return waited.indexOf(story.subject) >= 0; }) };
    });
    var rank = function (one) { return one.waited ? 0 : one.fresh && !one.total ? 2 : 1; };
    var ranked = shown.slice().sort(function (a, b) {
      if (rank(a) !== rank(b)) { return rank(a) - rank(b); }
      if (rank(a) === 1) {
        var shareA = a.total ? a.landed / a.total : 0, shareB = b.total ? b.landed / b.total : 0;
        if (shareA !== shareB) { return shareA - shareB; }
        if (a.total - a.landed !== b.total - b.landed) { return (b.total - b.landed) - (a.total - a.landed); }
      }
      return a.index - b.index;
    });
    var ownLanded = all.filter(isLanded).length;
    var landed = shown.reduce(function (sum, one) { return sum + one.landed; }, ownLanded);
    var total = shown.reduce(function (sum, one) { return sum + one.total; }, all.length);
    var unread = shown.filter(function (one) { return !one.fresh; });
    var met = ownLanded === all.length && landed === total && !unread.length;
    return { drawn: ranked.slice(0, MOST_DRAWN), folded: ranked.slice(MOST_DRAWN), unread: unread, landed: landed, total: total,
      repositories: shown.length + 1, state: unread.length ? "unread" : met ? "" : "open" };
  }
  /** A sibling's turn: solid once it has stories and all have landed, dotted while it can't be read, dashed otherwise. */
  function siblingState(one) { return !one.fresh ? "unread" : one.total && one.landed === one.total ? "" : "open"; }
  /** A drawn sibling's row under this repository's lanes: its name and how far it has come, with no stops of its own. */
  function siblingRow(one) {
    var row = el("div", "line sibling" + (siblingState(one) ? " " + siblingState(one) : ""));
    row.style.setProperty("--c", "var(--edge)");
    var since = text(one.group.since);
    var when = since ? el("time", null, spoken(since) || since) : null;
    if (when) { when.setAttribute("datetime", since); }
    var said = one.fresh ? figures(el("span"), "sibling:" + one.name, one.landed + " of " + one.total + " landed")
      : !text(one.group.readAt) || !when ? el("span", null, "Never read") : add(el("span"), "Couldn't be read since ", when);
    add(row, add(el("div", "name"), el("b", null, one.name), said));
    var stops = el("div", "stops");
    stops.style.setProperty("--solid", siblingState(one) === "" ? "100%" : "0%");
    return add(row, stops);
  }
  /**
   * Each story as the trunk's tick draws it, from its stop's kind: landed, in the merge queue, in review, being built,
   * stopped, not started, or, once the delivery closed, not landed.
   */
  var TICK_STATES = { merged: "landed", now: "queued", ready: "queued", "ready pending": "queued", held: "stopped", later: "planned",
    unlanded: "unlanded", unknown: "unknown" };
  // Nearest to landing first: landed, then the queue (the piece being checked, then the ready ones, then a hand-over the lead
  // hasn't accepted), in review, being built, stopped, not started.
  var TICK_RANK = { merged: 0, now: 1, ready: 2, "ready pending": 3, review: 4, building: 5, held: 6, later: 7, unknown: 8, unlanded: 9 };
  var TICK_WORDS = { landed: "landed", queued: "in the merge queue", review: "in review", building: "being built", stopped: "stopped",
    planned: "not started", unlanded: "not landed", unknown: "stage not recognised" };
  function tickOf(doc, piece, nowSubject) {
    var kind = unlandedAtClose(doc, piece) ? "unlanded" : stopKind(piece, nowSubject);
    var state = kind === "active" ? (piece.item.stage === "review" ? "review" : "building") : TICK_STATES[kind] || "unknown";
    return { kind: kind, state: state, rank: kind === "active" ? TICK_RANK[state] : TICK_RANK[kind] };
  }
  /** Where the trunk's ticks fall: one per story, in groups of five, the run aiming at TICK_SPAN however many there are. */
  function tickLayout(count) {
    var gap = Math.max(TICK_LEAST, Math.min(TICK_MOST, TICK_SPAN / Math.max(count, 1)));
    var at = function (i) { return JOIN_X + TICKS_FROM + i * gap + Math.floor(i / TICK_GROUP) * GROUP_GAP; };
    return { gap: gap, at: at, end: count ? at(count - 1) : JOIN_X + TICKS_FROM };
  }
  /**
   * What each of the four steps before the delivery rejoins its target says now, in the words the record's facts give:
   * catching up with the target, the suite, the review and the rejoin. Null once it has rejoined.
   */
  function rejoinSaid(doc) {
    var ledger = rejoinLedger(doc);
    if (!ledger) { return null; }
    // A head a later fact moved on from says so in words, as the struck head's own name does.
    [].forEach.call(ledger.querySelectorAll(".rj-head.struck"), function (node) { node.textContent = node.getAttribute("aria-label"); });
    return [].map.call(ledger.querySelectorAll(".rj-said"), function (node) { return node.textContent; });
  }
  var STEP_TITLES = ["Main in", "Suite", "Review", "Change request"];
  /**
   * Beside the lanes, each lane's line turns into one main line, and the main line is the delivery's progress: one tick per
   * story, landed first, then the open ones nearest to landing first, each drawn by its state in its lane's colour, in
   * groups of five. A caret marks the story the queue lands next. A delivery rejoining its target then draws one bar of
   * its four steps, labelled with the step it is on, that step's status under it, and bends into the target at the finish
   * post, so the target is drawn once. Otherwise the post stands at the end of the ticks, and with siblings of the epic the
   * line runs on to the epic's post, where the drawn siblings turn in from their rows below. The finish lines are squares
   * beside the post's label. A tick, a step and a square each name themselves on hover and focus, and a tick lights its
   * lane; the caller wires that through `look`.
   */
  function mergeDrawing(doc, all, ends, epic, track, look) {
    var step = closedEntry(doc) ? null : integrationOf(doc);
    var rows = ends.length + (epic ? epic.drawn.length : 0);
    var trunkY = LINE_STOP_Y + (ends.length - 1) * LINE_PITCH / 2;
    var ticks = tickLayout(all.length);
    var barX = ticks.end + BAR_GAP, barEnd = barX + BAR_WIDTH;
    var postX = step ? barEnd + BAR_TO_POST : ticks.end + POST_GAP;
    var postY = step ? trunkY + TARGET_DROP : trunkY;
    var epicJoinX = postX + EPIC_JOIN_GAP, epicPostX = postX + EPIC_POST_GAP;
    // The post's label is centred on it, so the drawing reaches past the post far enough to hold it whole.
    var width = step ? postX + TARGET_RUN + 8 : epic ? epicPostX + 14 : postX + LABEL_HALF;
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "line-merge");
    var landed = all.filter(isLanded).length;
    var finish = list(doc.state.finish).filter(isObject);
    var met = finish.filter(function (line) { return line.met === true; }).length;
    var unreadNames = epic ? epic.unread.map(function (one) { return one.name; }) : [];
    var stops = step ? integrationStops(step) : [];
    var filled = stops.filter(function (state) { return state === "filled"; }).length;
    // A group, not an image: the ticks, steps and squares inside it are each reached and named.
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Every lane merges into one main line: " + landed + " of " + all.length + " pieces landed" +
      (finish.length ? ", " + met + " of " + finish.length + " finish lines met" : "") +
      (step ? ", then rejoins " + step.target + ": " + filled + " of 4 steps done" : "") +
      (epic ? "; the epic: " + epic.landed + " of " + epic.total + " pieces landed across " + epic.repositories + " repositories" +
        (unreadNames.length ? ", " + spokenList(unreadNames) + " can't be read" : "") +
        (epic.folded.length ? ", " + epic.folded.length + " more not drawn" : "") : ""));
    var allIn = landed === all.length;
    if (step) {
      var rejoined = Boolean(step.landed);
      // The main line runs past the ticks and the bar, and bends into the target at the post; solid as far as the work is in.
      var bend = "C" + (postX - 8) + " " + trunkY + " " + (postX - 14) + " " + postY + " " + postX + " " + postY;
      shape(svg, "path", { class: "mg-trunk open", d: "M" + (JOIN_X - 100) + " " + trunkY + "H" + (postX - 30) + bend });
      if (allIn) { shape(svg, "path", { class: "mg-trunk", d: "M" + (JOIN_X - 100) + " " + trunkY + "H" + (rejoined ? postX - 30 : barX - 10) }); }
      if (rejoined) { shape(svg, "path", { class: "mg-trunk", d: "M" + (postX - 30) + " " + trunkY + bend }); }
      shape(svg, "path", { class: "mg-target" + (rejoined ? " solid" : ""), d: "M" + postX + " " + postY + "H" + (postX + TARGET_RUN) });
      shape(svg, "text", { class: "mg-label mg-target-label", x: postX + TARGET_RUN, y: postY + 18, "text-anchor": "end" }, step.target);
    } else {
      shape(svg, "path", { class: "mg-trunk" + (allIn ? "" : " open"), d: "M" + JOIN_X + " " + trunkY + "H" + postX });
    }
    if (epic) { shape(svg, "path", { class: "mg-epic" + (epic.state ? " " + epic.state : ""), d: "M" + postX + " " + trunkY + "H" + epicPostX }); }
    ends.forEach(function (end, i) {
      var y = LINE_STOP_Y + i * LINE_PITCH;
      var turn = shape(svg, "path", { class: "mg-turn" + (end.done ? "" : " open"), "data-lane": i,
        d: "M0 " + y + "H12C" + (12 + 52) + " " + y + " " + (JOIN_X - 52) + " " + trunkY + " " + JOIN_X + " " + trunkY });
      turn.style.setProperty("--c", end.colour);
    });
    (epic ? epic.drawn : []).forEach(function (one, j) {
      var y = LINE_STOP_Y + (ends.length + j) * LINE_PITCH;
      var state = siblingState(one);
      shape(svg, "path", { class: "mg-sib" + (state ? " " + state : ""),
        d: "M0 " + y + "H12C" + (epicJoinX - 78) + " " + y + " " + (epicJoinX - 38) + " " + trunkY + " " + epicJoinX + " " + trunkY });
    });
    shape(svg, "circle", { class: "mg-join", cx: JOIN_X, cy: trunkY, r: 4 });
    if (epic) { shape(svg, "circle", { class: "mg-epic-join", cx: epicJoinX, cy: trunkY, r: 4 }); }
    // The ticks: landed first, then the open ones nearest to landing first, each kind in the lanes' order, then the record's.
    var shown = all.map(function (piece, order) {
      var tick = tickOf(doc, piece, look.nowSubject);
      return { piece: piece, order: order, lane: look.laneOf(piece), tick: tick };
    }).sort(function (a, b) { return a.tick.rank - b.tick.rank || a.lane - b.lane || a.order - b.order; });
    var next = null, stopped = 0;
    shown.forEach(function (one, i) {
      var x = ticks.at(i), key = subjectOf(one.piece.item), tick = one.tick;
      var colour = look.colours[one.piece.item.lane || ""];
      // A story that landed since the last draw lands on the line, as its stop once did.
      var landing = tick.state === "landed" && prev.stages && prev.stages[key] && prev.stages[key] !== "merged" && !reduced();
      var g = shape(svg, "g", { class: "mg-tick " + tick.state + (landing ? " landing" : ""), "data-subject": key, "data-key": page.key("tick", key), "data-lane": one.lane, "data-state": tick.state,
        tabindex: 0, role: "button", "aria-pressed": String(look.selected() === key),
        // Named in the stop's own words, so a story folded out of its row says no less than its stop did.
        "aria-label": titleOf(doc, one.piece.item) + ", " + key + ", " + (tick.state === "unlanded" ? "Not landed" : pieceWords(doc, one.piece.item)) });
      g.style.setProperty("--c", colour);
      shape(g, "rect", { class: "mg-tick-hit", x: x - ticks.gap / 2, y: trunkY - 16, width: ticks.gap, height: 32 });
      if (tick.state === "building") { shape(g, "rect", { class: "mg-tick-half", x: x - 2.5, y: trunkY, width: 5, height: 10, rx: 1 }); }
      shape(g, "rect", { class: "mg-tick-mark", x: x - 2.5, y: trunkY - 10, width: 5, height: 20, rx: 2 });
      if (tick.state === "stopped") { stopped += 1; shape(g, "path", { class: "mg-flag", d: "M" + x + " " + (trunkY - 13) + "l-4 -6h8z" }); }
      // The queue lands its piece being checked next, or else its first ready one.
      if (!next && (tick.kind === "now" || tick.kind === "ready")) { next = { x: x, key: key }; }
      look.wire(g, one.piece, key + " · " + TICK_WORDS[tick.state], titleOf(doc, one.piece.item));
    });
    var count = figureText(svg, "merge-landed", { class: "mg-count mg-tally", x: JOIN_X + TICKS_FROM - 3, y: trunkY - 22 },
      landed + " of " + all.length + " landed");
    if (stopped) { shape(count, "tspan", { class: "mg-count mg-stopped", dx: 6 }, "· " + stopped + " stopped"); }
    if (next) {
      shape(svg, "path", { class: "mg-caret", d: "M" + next.x + " " + (trunkY + 14) + "l-4 6h8z" });
      shape(svg, "text", { class: "mg-why mg-next", x: next.x - 4, y: trunkY + 32 }, "next: " + next.key);
    }
    // How far below the main line the drawing reaches: the ticks' own boxes, the caret's words, the bar's status and the target.
    var below = trunkY + (next ? 36 : 16);
    if (step) {
      // The bar: the four steps, the current step and its status beneath it.
      var said = rejoinSaid(doc) || [];
      var current = -1;
      stops.forEach(function (state, i) { if (current < 0 && state !== "filled") { current = i; } });
      // Every step in but not yet rejoined: it waits on its last step, the change request, and says so.
      if (current < 0 && !rejoined) { current = stops.length - 1; }
      shape(svg, "text", { class: "mg-step-label mg-bar-label", x: barX, y: trunkY + 18 },
        "Rejoin " + step.target + (rejoined ? " · done" : " · " + STEP_TITLES[current].toLowerCase() + (stops[current] === "running" ? " running" : "")));
      var segment = (BAR_WIDTH - 6) / 4;
      // A timed suite's step is drawn wider, so the time it fills reads at a glance; the other three share the rest.
      var timed = rejoined ? null : suiteTimed(step);
      var widths = INTEGRATION_STOPS.map(function (name) {
        return !timed ? segment : name === "suite" ? (BAR_WIDTH - 6) * (1 - 3 * TIMED_SHARE) : (BAR_WIDTH - 6) * TIMED_SHARE;
      });
      var lefts = widths.map(function (width, i) { return barX + widths.slice(0, i).reduce(function (sum, w) { return sum + w + 2; }, 0); });
      stops.forEach(function (state, i) {
        // A step says what its latest fact shows; once rejoined the ledger has no words left, and every step is done.
        var words = said[i] || (state === "filled" ? "Done." : "Not yet.");
        var r = shape(svg, "rect", { class: "mg-step " + state, x: lefts[i], y: trunkY - 5, width: widths[i], height: 10, rx: 2,
          tabindex: 0, role: "img", "data-step": INTEGRATION_STOPS[i], "data-key": page.key("step", INTEGRATION_STOPS[i]), "data-state": state, "aria-label": STEP_TITLES[i] + ": " + words });
        if (timed && INTEGRATION_STOPS[i] === "suite") { timedSuite(svg, step, state, lefts[i], trunkY, widths[i]); }
        // Only the step the bar is on reads "now".
        look.wire(r, null, STEP_TITLES[i] + " · " + (i === current ? "now" : state === "filled" ? "done" : "later"), words);
      });
      var running = rejoined ? null : suiteRunning(step), runFor = running ? suiteRunWords(running) : "";
      var sentence = rejoined ? "Rejoined " + step.target + "."
        : running && stops[current] === "running" ? "The full suite is running at " + short(running.data.head) + "." + (runFor ? " " + runFor + "." : "")
        : STEP_TITLES[current] + ". " + (said[current] || "");
      var statusY = trunkY + (next ? 46 : 34), statusLineHeight = 14;
      var saidLines = wrapWords(sentence, 34).slice(0, 3);
      saidLines.forEach(function (line, k) {
        shape(svg, "text", { class: "mg-why mg-step-said", x: barX, y: statusY + k * statusLineHeight }, line);
        below = Math.max(below, statusY + 4 + k * statusLineHeight);
      });
      // The open change request into the target, the operator's to merge, opens from under the bar.
      var request = !rejoined && standing(step, "requested") ? step.latest.requested : null;
      if (request && text(request.data.changeRequest)) {
        var target = changeTarget(request.data.changeRequest);
        var y = statusY + saidLines.length * statusLineHeight + 2;
        var holder = target ? shape(svg, "a", { class: "mg-change", href: target.href, target: "_blank", rel: "noopener noreferrer",
          "aria-label": "Open the change request into " + step.target + " " + target.label }) : svg;
        shape(holder, "text", { class: "mg-why mg-change-label", x: barX, y: y },
          "Change request " + (target ? target.label : text(request.data.changeRequest)));
        below = Math.max(below, y + 4);
      }
      below = Math.max(below, postY + 22);
    }
    // The finish post, its label, and the finish lines as squares beside the label, with how many are met.
    shape(svg, "path", { class: "mg-post", d: "M" + postX + " " + (postY - 20) + "V" + (postY + 20) });
    // Over the post, the label sits higher to leave the squares room, or to stand clear of a tally that reaches under it.
    var tallyNode = svg.querySelector("text.mg-tally");
    var tallyEnd = tallyNode ? Number(tallyNode.getAttribute("x")) + tallyNode.textContent.length * TALLY_CHAR : -Infinity;
    var raised = finish.length || (!step && tallyEnd > postX - LABEL_HALF);
    shape(svg, "text", { class: "mg-label", x: postX, y: postY - (raised ? 52 : 30), "text-anchor": "middle" }, "Finish line");
    if (finish.length) {
      var squaresX = postX - (finish.length * 11) / 2 - 12;
      finish.forEach(function (line, k) {
        var name = text(line.text) || text(line.id) || "Finish line " + (k + 1);
        var square = shape(svg, "rect", { class: "mg-square " + (line.met === true ? "met" : "unmet"), x: squaresX + k * 11, y: postY - 44,
          width: 8, height: 8, rx: 1.5, tabindex: 0, role: "img", "data-key": page.key("finish", text(line.id) || name), "aria-label": name + (line.met === true ? ", met" : ", not met") });
        look.wire(square, null, "Finish line · " + (line.met === true ? "met" : "not met"), name);
      });
      figureText(svg, "merge-met", { class: "mg-count", x: squaresX + finish.length * 11 + 3, y: postY - 37 }, met + "/" + finish.length);
    }
    var top = MERGE_TOP;
    if (epic) {
      shape(svg, "path", { class: "mg-epic-post" + (epic.state ? " " + epic.state : ""), d: "M" + epicPostX + " " + (trunkY - 20) + "V" + (trunkY + 20) });
      // Above the epic's post, bottom up: its count, what can't be read, and its label.
      var words = unreadNames.length === 1 ? unreadNames[0] : unreadNames.length ? unreadNames.length + " can't be read" : "";
      var stack = [["mg-label" + (unreadNames.length ? " unread" : ""), unreadNames.length ? "Epic ?" : "Epic"]];
      if (words) { stack.push(["mg-count", words]); }
      stack.forEach(function (line, k) {
        shape(svg, "text", { class: line[0], x: epicPostX + 3, y: trunkY - 30 - (stack.length - k) * 14, "text-anchor": "end" }, line[1]);
      });
      figureText(svg, "merge-epic", { class: "mg-count", x: epicPostX + 3, y: trunkY - 30, "text-anchor": "end" }, epic.landed + "/" + epic.total + " landed");
      if (epic.folded.length) {
        var more = "+" + epic.folded.length + " more";
        var chip = el("span", "more-chip", more);
        chip.setAttribute("role", "img");
        chip.setAttribute("aria-label", more + ": " + spokenList(epic.folded.map(function (one) { return one.name; })));
        chip.style.setProperty("--chip-y", (trunkY + 28) + "px");
        track.appendChild(chip);
      }
    }
    // The room above the line holds every word the merge draws.
    [].forEach.call(svg.querySelectorAll("text"), function (node) {
      var size = TEXT_SIZE[node.getAttribute("class").split(" ")[0]] || 0;
      top = Math.max(top, Math.ceil(size - Number(node.getAttribute("y"))));
    });
    // Words above the line overflow the drawing into the room the track keeps above it; whatever the drawing holds below
    // its last row, the track keeps room for under it.
    var rowsHeight = (rows - 1) * LINE_PITCH + LINE_STOP_Y * 2;
    var height = Math.max(rowsHeight, below);
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    track.style.setProperty("--merge-width", width + "px");
    track.style.setProperty("--merge-top", top + "px");
    track.style.setProperty("--merge-below", (height - rowsHeight + 16) + "px");
    // Where the motion runs: the trunk's stream ends where the ticks do.
    svg.geometry = { width: width, joinX: JOIN_X, streamEnd: step ? barX - 10 : postX, trunkY: trunkY };
    return svg;
  }
  /** Words wrapped at about `width` characters a line. */
  function wrapWords(sentence, width) {
    var lines = [], line = "";
    text(sentence).split(" ").filter(Boolean).forEach(function (word) {
      if (line && (line + " " + word).length > width) { lines.push(line); line = word; } else { line = line ? line + " " + word : word; }
    });
    if (line) { lines.push(line); }
    return lines;
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
  function drawFlow(track, flows, laneCount, running, geometry) {
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
    var mergeX = width - geometry.width, joinX = mergeX + geometry.joinX;
    var finishX = mergeX + geometry.streamEnd;
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
  /**
   * Before it rejoins: one row each for catching up with the target, the suite, the review and the rejoin, each saying
   * what its latest fact shows at the head it names, struck through once a later fact moved the integration on.
   */
  function rejoinHead(step, head, superseded) {
    var node = el(superseded ? "s" : "code", "rj-head" + (superseded ? " struck" : ""), short(head));
    if (superseded) { node.setAttribute("aria-label", short(head) + ", since moved on"); }
    return node;
  }
  function rejoinRow(name, label, said) {
    var row = add(el("li", "rj-row rj-" + name), el("span", "rj-name", label));
    row.setAttribute("data-row", name);
    return add(row, add.apply(null, [el("span", "rj-said")].concat(said)));
  }
  function suiteWords(entry) {
    var data = entry.data;
    var took = typeof data.seconds === "number" ? spokenDuration(data.seconds) : "";
    var against = typeof data.budgetSeconds === "number" ? " against a budget of " + spokenDuration(data.budgetSeconds) : "";
    var words = { passed: "passed", "over-budget": "over-budget", failed: "failed", unreadable: "unreadable" }[data.result] || "failed";
    return words + (took ? " in " + took : "") + against;
  }
  function rejoinLedger(doc) {
    var step = integrationOf(doc);
    if (!step || step.landed) { return null; }
    var rows = [];
    // Caught up: every merge of the target, the last one standing.
    var merges = step.merges;
    rows.push(rejoinRow("caught", "Caught up", merges.length ? [plural(merges.length, "time") + ", at "].concat(
      merges.reduce(function (heads, entry, i) {
        return heads.concat(i ? [", "] : [], [rejoinHead(step, entry.data.head, i < merges.length - 1)]);
      }, []), list(merges[merges.length - 1].data.conflicts).length
        ? [", resolving " + plural(list(merges[merges.length - 1].data.conflicts).length, "conflict") + " by hand"] : [])
      : ["Not yet: the lead merges " + step.target + " in and records it."]));
    var suite = step.latest.suite, running = suiteRunning(step), runFor = running ? suiteRunWords(running) : "";
    rows.push(rejoinRow("suite", "Suite", running ? ["running at ", rejoinHead(step, running.data.head, false), runFor ? " for " + runFor : ""]
      : suite ? [suiteWords(suite) + " at ", rejoinHead(step, suite.data.head, !standing(step, "suite"))]
      : ["Not yet: the lead runs the full suite at the integration's head."]));
    var round = step.latest.round;
    if (round) {
      var verdicts = isObject(round.data.verdicts) ? round.data.verdicts : {};
      var lenses = Object.keys(verdicts);
      var asking = lenses.filter(function (lens) { return verdicts[lens] === "changes"; });
      var marks = el("span", "rj-dots");
      lenses.forEach(function (lens) {
        var dot = el("i", "rj-dot " + (verdicts[lens] === "approve" ? "approve" : "changes"));
        dot.setAttribute("title", lens + ": " + verdicts[lens]);
        marks.appendChild(dot);
      });
      rows.push(rejoinRow("review", "Review", ["round " + round.data.round + " ", marks,
        asking.length ? asking.join(" and ") + (asking.length === 1 ? " asks" : " ask") + " for changes, at " : "every lens approves, at ",
        rejoinHead(step, round.data.head, !standing(step, "round"))]));
    } else {
      rows.push(rejoinRow("review", "Review", ["Not yet: the integration review records its first round."]));
    }
    // Rejoin: ready only when a passing suite and an approving round stand at the current head.
    var suiteOk = standing(step, "suite") && suitePasses(suite), roundOk = standing(step, "round") && roundApproves(round);
    var said;
    if (suiteOk && roundOk) {
      var request = standing(step, "requested") ? step.latest.requested : null;
      said = ["Ready. "].concat(request ? ["The change request ", changeLink(request.data.changeRequest), " is open at ",
        rejoinHead(step, request.data.head, false), ". "] : ["A change request into " + step.target + " can open. "],
        [collabOf(doc) ? "The merge is " + (rejoinOwner(doc) ? rejoinOwner(doc) + "'s." : "yours.") : "The merge is the operator's."]);
    } else if (suitePasses(suite) && roundApproves(round) && text(suite.data.head) !== text(round.data.head)) {
      said = ["Needs a passing suite and an approving round at one head."];
    } else {
      var missing = [suiteOk ? "" : "a passing suite at the current head", roundOk ? "" : (suiteOk ? "an approving round at the current head" : "an approving round at it")].filter(Boolean);
      said = ["Needs " + missing.join(" and ") + "."];
    }
    rows.push(rejoinRow("rejoin", "Rejoin", said));
    return add(el("div", "rejoin"), el("h3", "rejoin-title", "Before it rejoins"), add.apply(null, [el("ul", "rejoin-rows")].concat(rows)));
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
    // Every lane row is the same width whatever its story count: its landed stories fold into one segment, at most three
    // open ones keep a stop, and the rest fold into "+n". Every story is a tick on the main line past the join.
    var box = el("div", "lines");
    var track = el("div", "lines-track");
    track.style.setProperty("--most", String(ROW_SLOTS));
    box.appendChild(track);
    var stages = Object.create(null);
    var folds = Object.create(null);
    var buttons = [];
    var ends = [];
    // Keys that all share a prefix such as "V26-" show without it on the line, so a lane's stops fit beside the merge; the
    // full key stays the link's accessible name and its data.
    var prefix = keyPrefix(all);
    var flows = [];
    // Lighting a lane dims every other lane's row, turn and ticks; the tip names what the pointer or focus is on.
    var tip = el("div", "mg-tip");
    tip.hidden = true;
    tip.setAttribute("aria-hidden", "true");
    // The lit lane is kept in the store, so a redraw under the pointer leaves it lit.
    function light(lane) {
      lit.set(lane);
      track.classList.toggle("lit", lane !== null);
      [].forEach.call(track.querySelectorAll("[data-lane]"), function (node) {
        node.classList.toggle("on", lane !== null && node.getAttribute("data-lane") === String(lane));
      });
    }
    function name(node, head, body) {
      if (head === null) { tip.hidden = true; return; }
      tip.replaceChildren(el("b", null, head), document.createTextNode(body));
      tip.hidden = false;
      if (typeof node.getBoundingClientRect === "function" && typeof track.getBoundingClientRect === "function") {
        var at = node.getBoundingClientRect(), origin = track.getBoundingClientRect();
        tip.style.left = Math.max(0, at.left - origin.left - 20) + "px";
        tip.style.top = (at.bottom - origin.top + 8) + "px";
      }
    }
    var look = {
      colours: colours, nowSubject: nowSubject,
      laneOf: function (piece) { return lanes.indexOf(piece.item.lane || ""); },
      selected: selected,
      // A tick, a step or a square names itself on hover and focus, and lights its lane where it has one. Focus, a click,
      // Enter or Space on a tick also opens its piece's panel, as a stop does, so a story folded out of its row is still
      // one step away; pointing only names and lights.
      wire: function (node, piece, head, body) {
        var lane = node.getAttribute("data-lane");
        var point = function () { name(node, head, body); light(lane === null ? null : Number(lane)); };
        var on = function () {
          point();
          if (piece) { picked.set(subjectOf(piece.item)); opener.set(node.getAttribute("data-key")); showDetail(doc, piece, colours, nowSubject); }
        };
        var off = function () { name(node, null); light(null); };
        node.addEventListener("mouseenter", point);
        node.addEventListener("focus", on);
        node.addEventListener("click", on);
        node.addEventListener("mouseleave", off);
        node.addEventListener("blur", off);
        if (piece) {
          node.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); on(); }
          });
        }
      }
    };
    lanes.forEach(function (lane, laneIndex) {
      var items = all.filter(function (piece) { return (piece.item.lane || "") === lane; });
      var row = el("div", "line");
      row.style.setProperty("--c", colours[lane]);
      row.setAttribute("data-lane", String(laneIndex));
      row.addEventListener("mouseenter", function () { light(laneIndex); });
      row.addEventListener("mouseleave", function () { light(null); });
      var landedHere = items.filter(isLanded).length;
      var open = items.filter(function (piece) { return !isLanded(piece); });
      // The piece being checked keeps its stop whatever its place in the lane, so the line's stream still fires from it.
      var checked = open.filter(function (piece) { return stopKind(piece, nowSubject) === "now"; })[0];
      var shownOpen = open.slice(0, OPEN_STOPS);
      if (checked && shownOpen.indexOf(checked) < 0) { shownOpen = shownOpen.slice(0, OPEN_STOPS - 1).concat([checked]); }
      ends.push({ colour: colours[lane], done: landedHere === items.length });
      add(row, add(el("div", "name"), el("b", null, lane ? names[lane] || lane : noLane(doc)), figures(el("span"), "lane:" + lane, landedHere + " of " + items.length + " landed")));
      var stops = el("div", "stops");
      items.forEach(function (piece) { stages[subjectOf(piece.item)] = unlandedAtClose(doc, piece) ? "unlanded" : stopKind(piece, nowSubject); });
      // The landed stories, folded into one segment that grows with their count, up to a slot's width.
      if (landedHere) {
        var grew = prev.folds && typeof prev.folds[lane] === "number" && prev.folds[lane] < landedHere && !reduced();
        var fold = el("div", "seg fold" + (grew ? " grew" : ""));
        fold.style.setProperty("--landed", String(Math.min(landedHere, 5)));
        add(fold, el("div", "fold-bar"), el("div", "fold-label", landedHere + " in"));
        fold.setAttribute("aria-label", plural(landedHere, "piece") + " landed");
        stops.appendChild(fold);
      }
      shownOpen.forEach(function (piece, i) {
        var kind = stages[subjectOf(piece.item)];
        var key = subjectOf(piece.item);
        var seg = el("div", "seg" + (kind === "active" && !(i === 0 && landedHere) ? " incoming" : ""));
        var button = withKey(el("button", "stop-btn " + kind), "stop", key);
        button.type = "button";
        button.setAttribute("data-subject", key);
        var waiting = kind === "unlanded" ? [] : waitWords(doc, key);
        button.setAttribute("aria-label", titleOf(doc, piece.item) + ", " + (kind === "unlanded" ? "Not landed" : pieceWords(doc, piece.item)) +
          (waiting.length ? ". " + waiting.join(" ") : ""));
        button.setAttribute("aria-pressed", String(selected() === key));
        var show = function () { picked.set(key); opener.set(button.getAttribute("data-key")); showDetail(doc, piece, colours, nowSubject); };
        button.addEventListener("click", show);
        // Pointing at a stop lights its lane; only a click or keyboard focus picks it.
        button.addEventListener("mouseenter", function () { light(laneIndex); });
        button.addEventListener("mouseleave", function () { light(null); });
        button.addEventListener("focus", show);
        button.addEventListener("focus", function () { light(laneIndex); });
        button.addEventListener("blur", function () { light(null); });
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
      if (open.length > OPEN_STOPS) {
        var rest = open.filter(function (piece) { return shownOpen.indexOf(piece) < 0; }).map(function (piece) { return subjectOf(piece.item); });
        var more = el("div", "seg more", "+" + rest.length);
        more.setAttribute("role", "img");
        more.setAttribute("aria-label", rest.length + " more open: " + spokenList(rest));
        stops.appendChild(more);
      }
      folds[lane] = landedHere;
      // The lane's rule is solid through its folded landed stories, and all the way once every story in it has landed.
      var used = (landedHere ? 1 : 0) + shownOpen.length + (open.length > OPEN_STOPS ? 1 : 0);
      var solid = landedHere === items.length ? 100 : landedHere ? (ROW_SLOTS - used + 1) * 100 / ROW_SLOTS : 0;
      stops.style.setProperty("--solid", solid + "%");
      add(row, stops);
      track.appendChild(row);
    });
    // A delivery rejoining its target draws that step past the join instead of the epic's post; the post comes back once
    // the delivery closes and the step leaves the line.
    var rejoins = !closedEntry(doc) && Boolean(integrationOf(doc));
    if (rejoins) { track.classList.add("rejoining"); }
    // The epic's drawn siblings have rows under this repository's lanes, on the same pitch.
    var epic = rejoins ? null : epicOf(doc, all);
    (epic ? epic.drawn : []).forEach(function (one) { track.appendChild(siblingRow(one)); });
    var merge = mergeDrawing(doc, all, ends, epic, track, look);
    track.insertBefore(merge, track.querySelector(".more-chip"));
    track.appendChild(tip);
    var detail = el("div", "detail");
    detail.id = "detail";
    detail.setAttribute("aria-live", "polite");
    var legend = el("div", "line-legend");
    // A row's landed stories fold into one bar, so no line joins two landed stops any more: the key has no entry for one.
    (closedEntry(doc) ? [["f", "Landed"], ["u", "Not landed"]]
      : [["f", "Landed"], ["n", "Being checked"], ["r", "Ready to check"], ["a", "In progress"], ["d", "Not started"], ["p", "Waiting for the lead to accept"], ["h", "Stopped"]]).forEach(function (entry) {
      legend.appendChild(add(el("span"), el("i", entry[0]), entry[1]));
    });
    nodes.push(box, detail, legend);
    inner.replaceChildren.apply(inner, nodes);
    // Where the line scrolls sideways, as on a phone, it opens at its end, where the lanes merge and the newest work is,
    // and keeps the reader's own place on later redraws.
    // Kept as the distance from the right edge, where the newest work is, so a change of width still shows it.
    var stillLit = lit.get();
    if (typeof stillLit === "number") { light(stillLit); }
    page.sideways("line", box);
    prev.stages = stages;
    prev.folds = folds;
    // The motion is measured from the drawn rows, so it is laid once they are in the page, and again when their width changes.
    if (prev.flowWatch) { prev.flowWatch.disconnect(); prev.flowWatch = null; }
    if (!closedEntry(doc)) {
      var layFlow = function () {
        flows.forEach(function (flow) { flow.left = flow.stops.getBoundingClientRect().left; });
        drawFlow(track, flows, lanes.length, current ? list(current.steps).filter(function (step) { return isObject(step) && step.state === "running"; }).length : 0,
          merge.geometry);
      };
      layFlow();
      if (typeof ResizeObserver === "function") { prev.flowWatch = new ResizeObserver(layFlow); prev.flowWatch.observe(track); }
    }
    var pick = all.filter(function (piece) { return subjectOf(piece.item) === selected(); })[0] ||
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
    // Where the story is, in one line, with how its review ended or the lane's own words under it.
    var state = unlanded ? null : storyState(doc, piece);
    var nodes = [owner, el("div", "d1", titleOf(doc, item)), line].concat(state ? [state] : [],
      unlanded ? [] : waitLines(doc, subject, "div", "d2 d-wait"));
    var inPlan = planStop(doc, subject, Boolean(state));
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
    // The state line says how the review stands, so the summary's own count of notes raised is left to the reports.
    var counted = state && run ? run.querySelector(".run-summary > p.run-story") : null;
    if (counted && !run.querySelector(".run-bead.is-running")) { counted.remove(); }
    if (run) { nodes.push(run); }
    var timeline = timelineFor(doc, item);
    if (timeline) { nodes.push(timeline); }
    var history = historyFor(doc, item);
    if (history) { nodes.push(history); }
    detail.replaceChildren.apply(detail, nodes);
    // A new piece's panel grows or shrinks to its height smoothly; a live redraw of the same piece does not move.
    if (switched && from) { runEaseHeight(detail, from); }
    Array.prototype.forEach.call(document.querySelectorAll(".stop-btn, .mg-tick"), function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-subject") === subject));
    });
  }

  /**
   * Where a story stands before it lands, as a state line with a dot: green while its lane moves it, amber while it
   * waits on something, grey while the merge queue checks it. Before hand-off, how its review ended; and, while its
   * lane is on it, the lane's own words and how long ago it said them. Null for a story that has landed, stopped or not
   * been started.
   */
  function storyState(doc, piece) {
    var item = piece.item, subject = item.subject, key = stageKey(item);
    var queued = !piece.planned && list(doc.state.queue).some(function (entry) { return isObject(entry) && entry.subject === subject; });
    var words, tone, review = null;
    if (queued && key === "pending") { words = "Waiting for the lead to accept"; tone = "waiting"; }
    else if (queued && (key === "queued" || IN_CHECKS[key])) { words = "Being checked before it lands"; tone = "checked"; }
    else if (!queued && (key === "building" || key === "review")) {
      var entries = journalFor(doc, subject, { "review.started": true, "review.round": true, "candidate.handed": true, "lane.assigned": true });
      var start = entries.filter(function (entry) { return entry.kind === "review.started"; }).slice(-1)[0];
      var rounds = entries.filter(function (entry) { return entry.kind === "review.round" && isObject(entry.data); });
      var last = rounds.slice(-1)[0];
      // A round ends when a round answers it or names it, or, as the run card reads it, when the story is handed off
      // or assigned again.
      var answered = start && entries.some(function (entry) {
        if ((entry.id || 0) <= (start.id || 0)) { return false; }
        if (entry.kind === "candidate.handed" || entry.kind === "lane.assigned") { return true; }
        return entry.kind === "review.round" && isObject(entry.data) && (list(entry.answers).indexOf(start.id) >= 0
          || (isObject(start.data) && entry.data.round === start.data.round));
      });
      if (start && !answered && isObject(start.data) && typeof start.data.round === "number") {
        words = "In review, round " + start.data.round; tone = "moving";
      } else if (last) {
        var verdicts = isObject(last.data.verdicts) ? Object.keys(last.data.verdicts).map(function (lens) { return last.data.verdicts[lens]; }) : [];
        var approved = verdicts.length > 0 && verdicts.every(function (verdict) { return verdict === "approve"; });
        if (approved) { words = "Reviewed, getting ready to hand off"; tone = "moving"; review = reviewEnded(doc, subject, rounds); }
        else { words = "Fixing review findings"; tone = "waiting"; }
      } else { words = "Being built"; tone = "moving"; }
    }
    if (!words) { return null; }
    var box = add(el("div", "d-state is-" + tone), el("div", "d-now", words));
    if (review) { box.appendChild(el("div", "d-said", review)); }
    var lane = list(doc.state.lanes).filter(function (entry) { return isObject(entry) && entry.id === item.lane; })[0];
    var took = lane ? list(doc.journal).filter(function (entry) {
      return isObject(entry) && entry.kind === "lane.assigned" && entry.lane === lane.id;
    }).slice(-1)[0] : null;
    if (lane && text(lane.focus) && took && took.subject === subject) {
      var at = seconds(lane.focusAt), ago = at === null ? null : Date.now() / 1000 - at;
      var said = add(el("div", "d-said" + (review ? " d-lane" : "")), text(lane.focus));
      if (ago !== null && ago >= 0) {
        var age = el("time", null, ago < 45 ? "just now" : duration(ago));
        age.setAttribute("datetime", text(lane.focusAt));
        add(said, " · ", age);
      }
      box.appendChild(said);
    }
    return box;
  }
  /**
   * How an approved review ended, from its recorded reports: "Approved on round 3 · all 8 notes fixed", or the notes
   * deferred to a follow-up, or still open. Without readable reports, the journal's counts say only what was raised.
   */
  function reviewEnded(doc, subject, rounds) {
    var last = rounds[rounds.length - 1];
    var number = typeof last.data.round === "number" ? last.data.round : rounds.length;
    var head = number === 1 ? "Approved on the first round" : "Approved on round " + number;
    var native = Object.prototype.hasOwnProperty.call(nativeReviews, subject) ? nativeReviews[subject] : null;
    // The reports are read only while they are of the story's latest recorded round, as the run card reads them.
    var boundary = journalFor(doc, subject, { "review.round": true, "review.recorded": true })
      .reduce(function (most, entry) { return Math.max(most, entry.id || 0); }, 0);
    var current = isObject(native) && native.unavailable !== true && native.journalId === boundary;
    var detail = current ? list(native.roundDetail).filter(isObject) : [];
    var raised = [], fixed = Object.create(null);
    detail.forEach(function (round) {
      list(round.raised).filter(isObject).forEach(function (note) { raised.push(note); });
      list(round.settled).filter(isObject).forEach(function (closure) { if (closure.state === "fixed") { fixed[closure.id] = true; } });
    });
    var notes = function (count) { return count + (count === 1 ? " note" : " notes"); };
    if (detail.length) {
      var deferred = raised.filter(function (note) { return note.state === "deferred"; }).length;
      var open = raised.filter(function (note) { return note.state === "unresolved"; }).length;
      // A note its lens withdrew is settled, not fixed.
      var mended = raised.filter(function (note) { return note.state === "resolved" && fixed[note.id]; }).length;
      var dropped = raised.length - deferred - open - mended;
      if (deferred) { return head + " · " + notes(deferred) + " deferred to a follow-up"; }
      if (open) { return head + " · " + notes(open) + " still open"; }
      if (!raised.length) { return head + " · nothing raised"; }
      if (dropped) { return head + " · " + notes(mended) + " fixed, " + dropped + " dropped"; }
      return head + " · " + (raised.length === 1 ? "its 1 note fixed" : "all " + raised.length + " notes fixed");
    }
    var total = 0;
    rounds.forEach(function (round) {
      var counts = isObject(round.data.findings) ? round.data.findings : {};
      ["P0", "P1", "P2", "P3"].forEach(function (severity) { total += typeof counts[severity] === "number" ? counts[severity] : 0; });
    });
    return head + " · " + (total ? notes(total) + " raised" : "nothing raised");
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
        box.appendChild(runSummaryNode(Object.assign({}, shown, { running: inFlight }), { commitHref: commitHref, issueHref: issueHref, fileHref: fileHref, tickets: Object.keys(LINKS.issues || {}), ticket: function (id) { return issue(id); }, followUpOf: followUpOf(doc, item.subject), lenses: VIEW.lenses, subject: item.subject }));
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
      nativeBox.appendChild(runSummaryNode(native, { commitHref: commitHref, issueHref: issueHref, fileHref: fileHref, tickets: Object.keys(LINKS.issues || {}), ticket: function (id) { return issue(id); }, followUpOf: followUpOf(doc, item.subject), lenses: VIEW.lenses, subject: item.subject }));
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
    box.appendChild(runSummaryNode(Object.assign({}, summary, { roundDetail: rounds }), { commitHref: commitHref, issueHref: issueHref, fileHref: fileHref, tickets: Object.keys(LINKS.issues || {}), ticket: function (id) { return issue(id); }, lenses: VIEW.lenses }));
    return box;
  }

  // A push redraws at once; the runs come from the view the server renders after it. The server's word that the runs
  // moved comes once their views are written, so that re-read waits for nothing.
  var runsAsked = null;
  function refreshRuns(wait) {
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
        var pushed = /<script type="application\/json" id="delivery-files">([^<]*)<\/script>/.exec(page);
        FILES = pushed ? readNeeds(pushed[1]) : null;
        var opened = /<script type="application\/json" id="delivery-left-open">([^<]*)<\/script>/.exec(page);
        LEFT_OPEN = opened ? readNeeds(opened[1]) : null;
        var drifted = /<script type="application\/json" id="delivery-integration">([^<]*)<\/script>/.exec(page);
        FORGE = drifted ? readForge(drifted[1]) : null;
        if (isObject(next)) { runs = next; safely(lastGood || shown, "runs"); }
      }).catch(function () { /* the runs already shown stay */ });
    }, typeof wait === "number" ? wait : 3000);
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
  /*
   * Who holds which files, when lanes share files by lines: the claimed files by area, the folder under the root they
   * sit in, each solid where its lane has changed it and hollow where it hasn't, and each shared file split by its
   * lanes' stretches. A shared file opens its reading: a ruler of the file with each lane's stretches and changes, and
   * per lane the functions it changed with their sizes. A lane whose branch can't be read is said so, and nothing of
   * its is drawn as changed.
   */
  var fileReading = null;
  var whoDrawn = null;
  // The view is drawn only once a branch read has something to say: a claimed file, or a read that failed. Before the
  // first read, or when the read lists nothing, there is nothing to fold away.
  function filesToShow() {
    if (!isObject(FILES)) { return false; }
    if (FILES.unreadable) { return true; }
    var lanes = isObject(FILES.lanes) ? FILES.lanes : {};
    return list(FILES.files).some(function (file) { return isObject(file) && typeof file.path === "string"; })
      || Object.keys(lanes).some(function (lane) { return isObject(lanes[lane]) && typeof lanes[lane].unreadable === "string"; });
  }
  function filesView(doc, colours) {
    var box = el("details", "files-fold");
    var body = el("div", "files-body");
    add(box, el("summary", null, "Who holds which files"), body);
    var fold = runFold(box, { key: "files" });
    if (FILES.unreadable) { body.appendChild(el("p", "say", "The lanes' changes can't be read.")); return fold; }
    var reads = isObject(FILES.lanes) ? FILES.lanes : {};
    var names = Object.create(null);
    list(doc.state.lanes).filter(isObject).forEach(function (lane) { names[text(lane.id)] = text(lane.title) || text(lane.id); });
    var named = function (lane) { return names[lane] || lane; };
    Object.keys(reads).forEach(function (lane) {
      var read = reads[lane];
      if (isObject(read) && typeof read.unreadable === "string") {
        body.appendChild(el("p", "files-unread", named(lane) + "'s branch " + text(read.branch) + " can't be read, so none of its changes are shown: " + read.unreadable));
      }
    });
    // A lane's read of a path, when its branch could be read and changed it.
    var changed = function (lane, path) {
      var read = reads[lane];
      if (!isObject(read) || typeof read.unreadable === "string") { return null; }
      return list(read.files).filter(function (file) { return isObject(file) && file.path === path; })[0] || null;
    };
    var unread = function (lane) { return isObject(reads[lane]) && typeof reads[lane].unreadable === "string"; };
    var areas = Object.create(null);
    var order = [];
    list(FILES.files).filter(function (file) { return isObject(file) && typeof file.path === "string"; }).forEach(function (file) {
      var area = file.path.indexOf("/") > 0 ? file.path.slice(0, file.path.indexOf("/")) : "";
      if (!areas[area]) { areas[area] = []; order.push(area); }
      areas[area].push(file);
    });
    var reading = null;
    order.forEach(function (area) {
      var shelf = el("div", "files-shelf");
      areas[area].forEach(function (file) {
        var base = file.path.slice(area ? area.length + 1 : 0);
        if (Array.isArray(file.spans)) {
          var lines = Math.max(1, file.lines || 0);
          var button = withKey(el("button", "file shared" + (fileReading === file.path ? " open" : "")), "file", file.path);
          button.setAttribute("type", "button");
          button.setAttribute("data-path", file.path);
          button.setAttribute("aria-expanded", fileReading === file.path ? "true" : "false");
          var bar = el("span", "file-split");
          file.spans.filter(isObject).forEach(function (span) {
            var touched = changed(span.lane, file.path);
            var stretch = el("span", "stretch " + (unread(span.lane) ? "unread" : touched ? "solid" : "hollow") + (span.regrant ? " regrant" : ""));
            stretch.style.setProperty("--c", colours[span.lane] || "var(--soft)");
            stretch.style.left = (100 * (span.start - 1) / lines) + "%";
            stretch.style.width = (100 * (span.end - span.start) / lines) + "%";
            stretch.setAttribute("data-lane", span.lane);
            stretch.setAttribute("title", named(span.lane) + ": lines " + span.start + "-" + (span.end - 1) + (span.regrant ? ", needs a regrant" : ""));
            bar.appendChild(stretch);
          });
          add(button, el("span", "file-name", base), bar);
          button.addEventListener("click", function () {
            fileReading = fileReading === file.path ? null : file.path;
            if (whoDrawn) { drawWho(whoDrawn[0], whoDrawn[1]); }
          });
          shelf.appendChild(button);
          if (fileReading === file.path) { reading = file; }
        } else {
          var owner = list(file.lanes)[0] || "";
          var mark = unread(owner) ? "unread" : changed(owner, file.path) ? "solid" : "hollow";
          var chip = el("span", "file whole " + mark, base);
          chip.style.setProperty("--c", colours[owner] || "var(--soft)");
          chip.setAttribute("data-path", file.path);
          chip.setAttribute("data-lane", owner);
          chip.setAttribute("title", file.path + " · " + list(file.lanes).map(named).join(", "));
          shelf.appendChild(chip);
        }
      });
      body.appendChild(add(el("div", "files-area"), el("h4", null, area ? area + "/" : "Top level"), shelf));
    });
    if (FILES.more) { body.appendChild(el("p", "say", FILES.more + " more claimed " + (FILES.more === 1 ? "file isn't" : "files aren't") + " shown.")); }
    if (reading) { body.appendChild(fileRead(reading, colours, changed, unread, named)); }
    return fold;
  }
  /** A shared file's reading: its ruler, with each lane's stretches and changes, and per lane what it changed. */
  function fileRead(file, colours, changed, unread, named) {
    var lines = Math.max(1, file.lines || 0);
    var box = add(el("div", "file-reading"), el("h4", null, file.path));
    box.setAttribute("data-path", file.path);
    var ruler = el("div", "ruler");
    var lanes = [];
    file.spans.filter(isObject).forEach(function (span) {
      if (lanes.indexOf(span.lane) < 0) { lanes.push(span.lane); }
      var stretch = el("span", "stretch" + (span.regrant ? " regrant" : ""));
      stretch.style.setProperty("--c", colours[span.lane] || "var(--soft)");
      stretch.style.top = (100 * (span.start - 1) / lines) + "%";
      stretch.style.height = (100 * (span.end - span.start) / lines) + "%";
      stretch.setAttribute("data-lane", span.lane);
      ruler.appendChild(stretch);
    });
    var rows = el("ul", "file-lanes");
    lanes.forEach(function (lane) {
      var row = add(el("li"), el("b", null, named(lane)));
      row.style.setProperty("--c", colours[lane] || "var(--soft)");
      row.setAttribute("data-lane", lane);
      if (unread(lane)) { rows.appendChild(add(row, " · its branch can't be read")); return; }
      var read = changed(lane, file.path);
      var changes = read ? list(read.changes).filter(isObject) : [];
      changes.forEach(function (change) {
        var tick = el("span", "tick" + (change.inside ? "" : " out"));
        tick.style.setProperty("--c", colours[lane] || "var(--soft)");
        tick.style.top = (100 * (change.start - 1) / lines) + "%";
        tick.style.height = Math.max(0.5, 100 * Math.max(change.added, 1) / lines) + "%";
        tick.setAttribute("data-lane", lane);
        ruler.appendChild(tick);
      });
      if (!changes.length) { rows.appendChild(add(row, " · no changes")); return; }
      var sizes = Object.create(null);
      var order = [];
      changes.forEach(function (change) {
        var name = typeof change.name === "string" && change.name ? change.name : "outside any function";
        if (!sizes[name]) { sizes[name] = { added: 0, removed: 0 }; order.push(name); }
        sizes[name].added += change.added;
        sizes[name].removed += change.removed;
      });
      var said = order.map(function (name) { return name + " +" + sizes[name].added + " \u2212" + sizes[name].removed; });
      rows.appendChild(add(row, " · " + said.join(", ")));
    });
    return add(box, ruler, rows);
  }
  /*
   * A lane's next story, as its card draws it. The state tool gives an idle lane's wait on its next story a blocker
   * when the cause is the plan's order or a file hold: the story it waits on, that story's lane, whether it is in the
   * merge queue, its phase in one word and the files a hold names. Those are grouped by blocker into one pill each,
   * nearest to landing first; every other wait keeps its own sentence.
   */
  var LANDING = ["merging", "checked", "checking", "synced", "syncing", "queued", "handed", "review", "building", "planned", "stopped"];
  function blockerStage(doc, subject) {
    // The last queue row for the story, as the state tool reads it.
    var rows = list(doc.state.queue).filter(function (item) { return isObject(item) && subjectOf(item) === subject; });
    var item = rows[rows.length - 1];
    if (item) { return item.pending === true || item.noChange ? "handed" : text(item.stage); }
    var entry = list(doc.state.planned).filter(function (planned) { return isObject(planned) && subjectOf(planned) === subject; })[0];
    return !entry ? "" : entry.noChange ? "handed" : text(entry.stage);
  }
  function nextOf(doc, laneId) {
    var blockers = [], byKey = Object.create(null), rest = [], next = "";
    waitsOf(doc, "waits", laneId).forEach(function (wait) {
      var key = typeof wait.blocker === "string" && wait.blocker ? wait.blocker : "";
      if (!key || typeof wait.on !== "string" || (next && wait.on !== next)) { rest.push(wait); return; }
      next = wait.on;
      var pill = byKey[key];
      if (!pill) {
        pill = byKey[key] = { key: key, lane: text(wait.onLane), queued: wait.queued === true, phase: text(wait.phase), files: [] };
        blockers.push(pill);
      }
      list(wait.files).forEach(function (path) { if (typeof path === "string" && pill.files.indexOf(path) < 0) { pill.files.push(path); } });
    });
    function rank(pill) { var at = LANDING.indexOf(blockerStage(doc, pill.key)); return at < 0 ? LANDING.length : at; }
    blockers = blockers.map(function (pill, index) { return { pill: pill, index: index, rank: rank(pill) }; })
      .sort(function (a, b) { return a.rank - b.rank || a.index - b.index; }).map(function (ranked) { return ranked.pill; });
    return { next: next, blockers: blockers, rest: rest };
  }
  var LOCK = '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>';
  function lockMark(label) {
    var mark = el("span", "lock");
    mark.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + LOCK + "</svg>";
    if (label) { mark.setAttribute("role", "img"); mark.setAttribute("aria-label", label); }
    return mark;
  }
  /** The block under a lane card: its next story once, a "Waiting on" row of one pill per blocker, and a legend. */
  function nextBlock(doc, found, colours) {
    var item = list(doc.state.planned).filter(function (entry) { return isObject(entry) && subjectOf(entry) === found.next; })[0];
    var head = add(el("p", "next-head"), el("span", "k", "Next"), " ", issue(found.next));
    if (item && titleOf(doc, item) !== found.next) { add(head, " \u00b7 " + titleOf(doc, item)); }
    var row = add(el("div", "waiting-on"), el("span", "k", "Waiting on"));
    var held = [];
    found.blockers.forEach(function (pill) {
      var node = el("span", "dep" + (pill.queued ? " queued" : ""));
      node.style.setProperty("--c", colours[pill.lane] || laneColour(pill.lane));
      var said = [pill.lane ? laneLabel(pill.lane) : "No lane", pill.queued ? "in the merge queue" : "still with its lane"];
      if (pill.files.length) { said.push("holds " + pill.files.join(", ")); }
      node.setAttribute("title", said.join(" \u00b7 "));
      add(node, el("span", "dot"), issue(pill.key), el("span", "phase", pill.phase));
      if (pill.files.length) {
        node.appendChild(lockMark("holds files " + found.next + " needs"));
        pill.files.forEach(function (path) { if (held.indexOf(path) < 0) { held.push(path); } });
      }
      row.appendChild(node);
    });
    var legend = el("div", "legend");
    if (found.blockers.some(function (pill) { return pill.queued; })) { legend.appendChild(add(el("span"), el("i", "dot filled"), "in the merge queue")); }
    if (found.blockers.some(function (pill) { return !pill.queued; })) { legend.appendChild(add(el("span"), el("i", "dot"), "still with its lane")); }
    if (held.length) {
      legend.appendChild(add(el("span"), lockMark(""), add(el("span"), "holds ", el("code", null, held.join(", ")), ", which " + found.next + " needs")));
    }
    return add(el("div", "next"), head, row, legend);
  }
  /*
   * A lane's own story once it is handed off: the latest of its stories still in the queue, unless the lane has
   * moved on to other work. The card then says where it went and follows it through the queue.
   */
  var QUEUE_PHASE = table({ queued: "queued", syncing: "syncing", synced: "syncing", checking: "checking", checked: "checked", merging: "merging" });
  function handedOf(doc, lane) {
    if (lane.state !== "idle" && lane.state !== "handed" && lane.state !== "waiting") { return null; }
    var mine = list(doc.state.queue).filter(function (item) {
      return isObject(item) && item.lane === lane.id && item.stage !== "merged" && item.stage !== "stopped";
    }).sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
    var item = mine[mine.length - 1] || null;
    if (!item || (lane.state === "waiting" && lane.waitingOn !== subjectOf(item))) { return null; }
    return item;
  }
  function roundsOf(doc, item) {
    var most = 0;
    journalFor(doc, subjectOf(item), { "review.round": true }).forEach(function (entry) {
      var round = isObject(entry.data) && typeof entry.data.round === "number" ? entry.data.round : 0;
      if (round > most) { most = round; }
    });
    return most || (typeof item.reportedRounds === "number" ? item.reportedRounds : 0);
  }
  function handedLine(doc, item) {
    var rounds = roundsOf(doc, item);
    // A hand-off the lead hasn't accepted yet leaves the queue row at its earlier head: the journal has the new one.
    var latest = journalFor(doc, subjectOf(item), { "candidate.handed": true }).pop();
    var head = latest && isObject(latest.data) && typeof latest.data.head === "string" && latest.data.head ? latest.data.head : item.head;
    return add(el("p", "doing"), issue(subjectOf(item)), " \u00b7 handed off at ", el("code", null, short(head)),
      rounds ? " after review round " + rounds : "");
  }
  /** The chip of a lane whose story is in the merge queue, with the runner's phase and how long it has been there. */
  function queueChip(doc, item, lane) {
    if (item.pending === true || !QUEUE_PHASE[item.stage]) { return null; }
    // Since its latest hand-off, or, for a record that journals none, since the lane stood handed over.
    var handed = journalFor(doc, subjectOf(item), { "candidate.handed": true }).pop();
    var then = handed ? seconds(handed.at) : lane.state === "handed" ? seconds(lane.since) : null;
    var since = then === null || Date.now() / 1000 < then ? "" : duration(Date.now() / 1000 - then);
    return [el("span", "chip queue", "In merge queue"), el("span", "on", QUEUE_PHASE[item.stage] + (since ? " \u00b7 " + since : ""))];
  }
  function drawWho(doc, all) {
    whoDrawn = [doc, all];
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
        var who = add(el("div"), el("p", "lane-name", laneLabel(text(lane.id))), el("h3", null, text(lane.title) || text(lane.id)));
        var doing = text(lane.focus) || text(focus[lane.id]);
        var handed = closed ? null : handedOf(doc, lane);
        if (handed) { who.appendChild(handedLine(doc, handed)); }
        else if (doing) { who.appendChild(el("p", "doing", doing)); }
        if (text(lane.focus) && !closed) { who.appendChild(rowAge(lane)); }
        var next = closed ? null : nextOf(doc, lane.id);
        if (next) { next.rest.forEach(function (wait) { who.appendChild(waitLine("p", "doing wait", wait)); }); }
        var inQueue = handed ? queueChip(doc, handed, lane) : null;
        var st = el("div", "st");
        if (closed) { st.appendChild(el("span", "chip done", "Finished")); /* what a lane last waited on or how long it has held is over */ }
        else if (inQueue) { add(st, inQueue[0], inQueue[1]); }
        else {
          st.appendChild(el("span", "chip" + (LANE_WORDS[lane.state] ? " " + lane.state : ""), LANE_WORDS[lane.state] || "Unknown"));
          if (lane.waitingOn) { st.appendChild(el("span", "on waiting", "waiting on " + text(lane.waitingOn))); }
          else if (heldFor(lane.since)) { st.appendChild(el("span", "on", heldFor(lane.since))); }
        }
        add(row, add(el("span", "swatch"), persona("lane")), who, st);
        if (next && next.blockers.length) { row.appendChild(nextBlock(doc, next, colours)); }
        people.appendChild(row);
      });
      nodes.push(people);
      prev.lanes = seen;
    } else if (!closed && all.length) {
      // Nobody has reported yet: who is expected, from the planned work's lanes, each with the piece it starts on.
      var expected = [];
      var byLane = Object.create(null);
      all.forEach(function (piece) {
        var lane = text(piece.item.lane);
        if (!lane) { return; }
        if (!byLane[lane]) { byLane[lane] = []; expected.push(lane); }
        byLane[lane].push(piece);
      });
      if (expected.length) {
        nodes.push(el("p", "say", plural(expected.length, "lane") + " will report here as " + (expected.length === 1 ? "it starts." : "they start.")));
        var waiting = el("ol", "expected");
        expected.forEach(function (lane) {
          var pieces = byLane[lane];
          var first = pieces[0];
          var key = subjectOf(first.item);
          var rings = el("span", "expected-rings");
          rings.setAttribute("aria-label", plural(pieces.length, "piece"));
          pieces.forEach(function () { rings.appendChild(el("i")); });
          var opener = add(el("span", "expected-first"), key ? issue(key) : null, key ? " \u00b7 " : null, titleOf(doc, first.item));
          var row = add(el("li"), add(el("span", "expected-lane"), add(el("span", "swatch"), persona("lane")), el("span", null, lane)), rings, opener);
          row.style.setProperty("--c", laneColour(lane));
          waiting.appendChild(row);
        });
        nodes.push(waiting);
      }
    }
    if (list(doc.state.laneRegions).filter(isObject).length && filesToShow()) { nodes.push(filesView(doc, colours)); }
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
        var title = withKey(el("button", "left-title", titleOf(doc, piece.item)), "left", key);
        title.type = "button";
        title.setAttribute("aria-label", "Show " + titleOf(doc, piece.item) + " on the line");
        var row = add(el("li"), ring, key ? issue(key) : null, title, el("span", "left-state", pieceWords(doc, piece.item)));
        waitLines(doc, key, "span", "left-wait").forEach(function (line) { row.appendChild(line); });
        row.addEventListener("click", function (event) {
          if (event.target && event.target.closest && event.target.closest("a")) { return; }
          var stop = [].filter.call(parts.line.querySelectorAll(".stop-btn"), function (button) { return button.getAttribute("data-subject") === key; })[0];
          if (!stop) { return; }
          stop.click();
          page.go({ chapter: "line", node: stop }, "link");
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
  var planAsApproved = false;
  var planRows = Object.create(null);
  // The plan's folds the reader keeps open, saved as one list of their names; each fold's own state is the page's store.
  var FOLD_KEY = (typeof VIEW.themeKey === "string" && VIEW.themeKey ? VIEW.themeKey.replace(/:theme$/, "") : "delivery-views") + ":plan-folds";
  var planFoldsSaved = page.state(FOLD_KEY, { saved: "json" });
  var planFoldNames = [];
  list(planFoldsSaved.get()).forEach(function (name) {
    if (typeof name !== "string") { return; }
    planFoldNames.push(name);
    page.state("fold:plan:" + name).set(true);
  });
  function rememberFolds() {
    planFoldsSaved.set(planFoldNames.filter(function (name) { return page.state("fold:plan:" + name).get() === true; }));
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
    var link = withKey(figures(el("a", "plan-now-link"), "plan-now", counts.proven + " of " + counts.total + " criteria proven"), "plan-now", "criteria");
    link.setAttribute("href", "#plan");
    return add(el("span", "plan-now-plan"), link, figures(el("span"), "plan-changes", " · " + timesChanged(list(plan.changes).length)));
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
    if (blocking.length) {
      flags.push({ title: blocking.length === 1 ? "An approval is blocking work" : blocking.length + " approvals are blocking work",
        approvals: blocking });
    }
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
  /**
   * An approval's short title and the rest of its text. The title is what comes before the text's first colon, or else
   * its first sentence, without a leading copy of the approval's own id; a text with neither is cut short.
   */
  function approvalParts(entry) {
    var words = text(entry.text).replace(/\s+/g, " ").trim();
    var id = text(entry.id);
    if (id && words.indexOf(id) === 0) { words = words.slice(id.length).replace(/^[.:\s-]+/, ""); }
    var colon = words.indexOf(": ");
    if (colon > 0 && colon <= 90) { return { title: words.slice(0, colon), rest: words.slice(colon + 2) }; }
    var stop = words.search(/\.\s/);
    if (stop > 0 && stop <= 120) { return { title: words.slice(0, stop), rest: words.slice(stop + 2) }; }
    if (words.length <= 120) { return { title: words.replace(/\.$/, ""), rest: "" }; }
    var cut = words.lastIndexOf(" ", 96);
    return { title: words.slice(0, cut > 40 ? cut : 96) + "…", rest: words };
  }
  /** The blocking approvals: who holds how many, then one folded row per approval, opened to read the whole of it. */
  function approvalList(entries) {
    var owners = [];
    var held = Object.create(null);
    // An approval held by several ("Product, Engineering") counts for each of them.
    entries.forEach(function (entry) {
      (text(entry.who) || "Unassigned").split(/\s*,\s*|\s+and\s+/).filter(Boolean).forEach(function (who) {
        if (!held[who]) { held[who] = 0; owners.push(who); }
        held[who] += 1;
      });
    });
    owners.sort(function (a, b) { return held[b] - held[a]; });
    var SHOWN = 4;
    var byOwner = el("p", "ap-owners");
    owners.slice(0, SHOWN).forEach(function (who, index) {
      if (index) { byOwner.appendChild(document.createTextNode(" · ")); }
      add(byOwner, el("span", "ap-owner-name", who), el("span", "ap-owner-count", String(held[who])));
    });
    if (owners.length > SHOWN) { byOwner.appendChild(el("span", "ap-owner-more", " · +" + (owners.length - SHOWN) + " more")); }
    var listing = el("ol", "ap-list");
    entries.forEach(function (entry) {
      var parts = approvalParts(entry);
      var row = el("details", "ap-row");
      var summary = el("summary");
      var id = text(entry.id);
      // An id that only numbers its row (handoff-3) says nothing a reader needs; a named one (PD1, O25) does.
      var shownId = id && !/^[a-z]+-\d+$/i.test(id) ? id : "";
      var who = text(entry.who);
      add(summary, el("span", "ap-id", shownId), el("span", "ap-title", parts.title), el("span", "ap-who", who === parts.title ? "" : who),
        el("span", "ap-caret"));
      add(row, summary, parts.rest ? el("p", "ap-rest", parts.rest) : null);
      if (!parts.rest) {
        // A row with nothing more to say never opens.
        row.classList.add("is-whole");
        withKey(summary, "approval", id || parts.title);
        summary.addEventListener("click", function (event) { event.preventDefault(); });
      } else {
        page.fold(row, { key: "approval:" + (id || parts.title) });
      }
      listing.appendChild(add(el("li"), row));
    });
    // What clears the flag. The operator doesn't run anything: they tell the agent leading the work, which records it.
    var how = el("p", "ap-how", "Tell the agent leading this which you approve; it records each one in the plan.");
    return add(el("div", "ap"), owners.length > 1 ? byOwner : null, listing, how);
  }
  function flagList(flags, closed) {
    var box = el("div", "plan-needs");
    if (!flags.length) { return closed ? null : add(box, el("p", "plan-calm", "Nothing in the plan needs attention right now.")); }
    var ul = el("ul");
    flags.forEach(function (flag) {
      // What is wrong, what it names, then what settles it. Nobody asked the reader for these: the page found the plan and
      // the record out of step, so the heading says so rather than "Needs you", which is the supervisor's request.
      ul.appendChild(add(el("li"), glyph("stop"), add(el("div"), el("div", "a1", flag.title), flag.why ? el("div", "a2", flag.why) : null,
        flag.approvals ? approvalList(flag.approvals) : null, flag.fix && !closed ? el("div", "a3", flag.fix) : null)));
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
  /** A plan decision as `{head, body}`: a headed one as the plan reader wrote it, or one text as a headline alone. */
  function decisionOf(entry) {
    if (typeof entry === "string") { return text(entry) ? { head: text(entry), body: [] } : null; }
    if (!isObject(entry) || !text(entry.head)) { return null; }
    var body = list(entry.body).map(function (block) {
      if (typeof block === "string") { return text(block) || null; }
      var items = isObject(block) ? list(block.items).map(text).filter(Boolean) : [];
      return items.length ? items : null;
    }).filter(Boolean);
    return { head: text(entry.head), body: body };
  }
  /** A decision's words through the page's one formatter, with "decision N", for a number the list holds, a link to it. */
  function decisionWords(words, count) {
    var nodes = [], last = 0, match, pattern = /\bdecision (\d+)\b/gi;
    function plain(part) { if (part) { nodes = nodes.concat(linked(part)); } }
    while ((match = pattern.exec(words)) !== null) {
      var n = Number(match[1]);
      if (n < 1 || n > count) { continue; }
      plain(words.slice(last, match.index));
      var ref = el("a", "pd-ref", match[0]);
      ref.href = "#plan-decision-" + n;
      ref.addEventListener("click", (function (to) { return function (event) { event.preventDefault(); openDecision(to); }; })(n));
      nodes.push(ref);
      last = match.index + match[0].length;
    }
    plain(words.slice(last));
    return nodes;
  }
  /** Opens a decision a reference names, and brings it to the reader. */
  function openDecision(n) {
    var row = document.getElementById("plan-decision-" + n);
    if (!row) { return; }
    // The decision's own fold stands for its row, so going to the row opens it and every fold holding it.
    page.go({ item: row.id }, "link");
    var summary = row.querySelector("summary");
    if (summary) { summary.focus({ preventScroll: true }); }
  }
  /**
   * The plan's decisions, numbered as the plan numbers them: each headline always shows, so the calls read at a glance,
   * and a decision with reasons opens to its paragraphs and bullets. One control opens or closes every one of them.
   */
  function decisionList(decisions) {
    var box = el("div", "plan-decided");
    var rows = el("ol", "plan-decisions");
    var boxes = [];
    var all = withKey(el("button", "pd-all"), "decisions", "all");
    all.type = "button";
    // Whether every decision is open, by the page's store, which a click has set before the fold has finished moving.
    function allOpen() { return boxes.every(function (entry) { return page.state("fold:decision:" + entry.getAttribute("data-n")).get() === true; }); }
    function sayAll() { all.textContent = allOpen() ? "Close all" : "Open all"; }
    decisions.forEach(function (decision, i) {
      var n = i + 1;
      var row = el("li", "pd");
      row.id = "plan-decision-" + n;
      var head = add.apply(null, [el("span", "pd-head")].concat(decisionWords(decision.head, decisions.length)));
      var line = [el("span", "pd-n", String(n)), head];
      if (!decision.body.length) {
        rows.appendChild(add(row, add.apply(null, [el("div", "pd-line")].concat(line))));
        return;
      }
      var body = el("div", "pd-body");
      decision.body.forEach(function (block) {
        if (typeof block === "string") { body.appendChild(add.apply(null, [el("p")].concat(decisionWords(block, decisions.length)))); return; }
        var bullets = el("ul");
        block.forEach(function (item) { bullets.appendChild(add.apply(null, [el("li")].concat(decisionWords(item, decisions.length)))); });
        body.appendChild(bullets);
      });
      var fold = el("details", "pd-fold");
      fold.setAttribute("data-n", String(n));
      boxes.push(fold);
      rows.appendChild(add(row, add(fold, add.apply(null, [el("summary", "pd-line")].concat(line)), body)));
      page.fold(fold, { key: "decision:" + n, of: row, onChange: sayAll, onOpen: sayAll });
    });
    if (boxes.length) {
      all.addEventListener("click", function () {
        var open = !allOpen();
        boxes.forEach(function (entry) { page.open(entry, open); });
        sayAll();
      });
      sayAll();
      box.appendChild(add(el("div", "pd-tools"), all));
    }
    return add(box, rows);
  }
  /** A fold the reader opens; open folds are remembered for this page. */
  function fold(name, label, count, body) {
    var box = el("details", "plan-fold");
    box.setAttribute("data-fold", name);
    if (planFoldNames.indexOf(name) < 0) { planFoldNames.push(name); }
    var summary = el("summary", null, label);
    if (count !== null) { summary.appendChild(el("span", "pf-count", String(count))); }
    add(box, summary, body);
    return page.fold(box, { key: "plan:" + name, onChange: rememberFolds, onOpen: function () {
      // Opened by a link or by the page as well as by a click, it is saved as the reader will find it.
      if (list(planFoldsSaved.get()).indexOf(name) < 0) { rememberFolds(); }
    } });
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
  /**
   * A plan preview's line, never "Approved": "Agent review only · 3 review rounds · still open · not approved by a person",
   * "… · not reviewed yet · …" before its first round, and "Handed to delivery <key> · …" once a delivery took it. A
   * planning record's names its endorsements: "Endorsed by Ama and Ben · …" once every approver seat endorsed the
   * commit it shows, and before that each seat that did, with one of an older commit said to be stale.
   */
  function previewLine(review) {
    var count = review ? reviewRounds(review) : 0;
    var rounds = !review ? "not reviewed yet" : count + (count === 1 ? " review round" : " review rounds") +
      (REVIEW_ENDINGS[review.state] ? " · " + REVIEW_ENDINGS[review.state] : "");
    var approval = plan.preview.approval;
    var endorsed = list(plan.preview.endorsements).filter(isObject);
    var named = function (items) {
      var labels = items.map(function (item) { return text(item.label); });
      return labels.length < 2 ? labels.join("") : labels.slice(0, -1).join(", ") + " and " + labels[labels.length - 1];
    };
    var current = endorsed.filter(function (item) { return item.commit === plan.preview.commit; });
    var stale = endorsed.filter(function (item) { return item.commit !== plan.preview.commit; });
    if (approval === "endorsed") {
      return el("p", "plan-approval", "Endorsed by " + named(current) + " · " + rounds +
        (stale.length ? " · stale endorsement by " + named(stale) : ""));
    }
    var handed = approval === "handed";
    return el("p", "plan-approval", (handed ? "Handed to delivery " + text(plan.preview.delivery) : "Agent review only") +
      " · " + rounds + (current.length ? " · endorsed by " + named(current) : "") +
      (stale.length ? " · stale endorsement by " + named(stale) : "") + (handed ? "" : " · not approved by a person"));
  }
  /** What the page left out of a plan too large to show whole; a preview's plan was never approved, so it says "the plan". */
  function clippedNote(preview) {
    var clip = PLAN.clipped;
    return el("p", "plan-clipped", "The plan was too large to show whole: " +
      [clip.changes ? "the text of the " + clip.changes + " oldest " + (clip.changes === 1 ? "change" : "changes") : "",
        clip.texts ? clip.texts + " other " + (clip.texts === 1 ? "text" : "texts") : ""].filter(Boolean).join(" and ") + " left out." +
      (clip.overShare ? (preview ? " The plan alone is over the page's share for it." : " The approved plan alone is over the page's share for it.") : ""));
  }
  function reviewFold(review) {
    var count = reviewRounds(review);
    var summary = { observational: true, observationKey: "plan-review", open: false, roundDetail: list(review.rounds).filter(isObject),
      planReview: { state: text(review.state), endedReason: text(review.endedReason) } };
    var box = el("div", "plan-review");
    box.appendChild(runSummaryNode(summary, { commitHref: commitHref, issueHref: issueHref, fileHref: fileHref, tickets: Object.keys(LINKS.issues || {}), ticket: function (id) { return issue(id); }, lenses: VIEW.lenses,
      preview: isObject(plan.preview) }));
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
        safely(lastGood || shown, "plan");
      } else {
        followPlan(planNamed);
      }
      return;
    }
    var older = isObject(block) && isObject(block.plan) && typeof block.plan.updated === "string" && seconds(block.plan.updated) !== null;
    planTries += 1;
    var behind = planBehind || !older || planTries >= PLAN_PATIENCE;
    if (behind !== planBehind) { planBehind = behind; safely(lastGood || shown, "plan"); }
    // Only a change of note drew, so ask again from here too; a second ask is refused while one is waiting.
    followPlan(planNamed);
  }
  // The new plan is only kept if the page can draw it: the plan in use goes back otherwise. A new plan is a new read, so
  // it draws at once, and the redraw transaction keeps the reader's place: past the chapter, a plan of another height
  // moves the window with what they were reading.
  function takePlan(block) {
    page.redraw(function () {
      var was = { PLAN: PLAN, plan: plan, named: planNamed };
      PLAN = block;
      plan = block.plan;
      planBehind = false;
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
        safely(lastGood || shown, "plan");
      }
    }, "read");
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
      var again = withKey(el("button", null, "Reload the page"), "plan", "reload");
      again.type = "button";
      again.addEventListener("click", function () { if (typeof location === "object" && typeof location.reload === "function") { location.reload(); } });
      behind = add(el("p", "plan-reload"), el("span", null, live ? "The plan has changed, and this page couldn't load the new one." : "The plan has changed since this page was made."), again);
      behind.setAttribute("role", "status");
    }
    var review = planReview();
    // A plan under agent review: no person has approved it, so nothing in the chapter reads as approved.
    var preview = isObject(plan.preview) ? plan.preview : null;
    if (preview) { planAsApproved = false; }
    nodes.push(title);
    if (behind) { nodes.push(behind); }
    nodes.push(el("p", "say plan-outcome", text(isObject(plan.outcome) ? plan.outcome.summary : "")));
    if (preview) { nodes.push(previewLine(review)); } else if (review) { nodes.push(approvalLine(review)); }
    var otherLayout = Boolean(preview) && preview.layout === "other";
    if (otherLayout) { nodes.push(el("p", "say plan-layout", "This plan isn't in the delivery layout, so it shows no stories.")); }
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
        withKey(li, "plan-row", text(unit.subject));
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
      var button = withKey(el("button", null, choice[1]), "plan-switch", choice[0] ? "approved" : "amended");
      button.type = "button";
      button.setAttribute("aria-pressed", String(planAsApproved === choice[0]));
      button.addEventListener("click", function () { planAsApproved = choice[0]; page.redraw(function () { drawPlan(shown); }, "reader"); });
      switcher.appendChild(button);
    });
    switcher.appendChild(el("span", "ps-when", "Approved " + (spoken(approved.at) || "at an unknown time") + (text(approved.review) ? " · " + text(approved.review) : "")));
    // A preview has one plan, the one under review, so there is nothing to switch between.
    add(all, preview ? null : switcher, add(el("div", "plan-grid-head"), el("div", null, "Unit"), el("div", null, "Criteria"), el("div", null, "Status")));
    var allProofs = planProofs(doc, shownUnits);
    shownUnits.forEach(function (unit) {
      var row = unitRow(allProofs[text(unit.subject)], kinds, planAsApproved);
      row.setAttribute("tabindex", "-1");
      withKey(row, "plan-unit-row", text(unit.subject));
      planRows["all:" + text(unit.subject)] = row;
      all.appendChild(row);
    });
    var folds = el("div", "plan-folds");
    if (!otherLayout) { folds.appendChild(fold("units", "All units", shownUnits.length, all)); }
    if (review) { folds.appendChild(reviewFold(review)); }
    if (preview) {
      var decisions = list(preview.decisions).map(decisionOf).filter(Boolean);
      // Before the plan records a decision, the fold says what will appear in it.
      folds.appendChild(fold("decisions", "Decisions", decisions.length ? decisions.length : null,
        decisions.length ? decisionList(decisions) : el("p", "waiting", "The plan's decisions will show here once it records them.")));
    }
    var changes = list(plan.changes).filter(isObject);
    var changeBox = el("div", "plan-changes");
    changeBox.appendChild(el("p", "waiting plan-scoped", scopeSentence(changes)));
    if (changes.length) { changeBox.appendChild(scopeChart(changes)); }
    var changeList = el("ul");
    changes.slice().reverse().forEach(function (change) { changeList.appendChild(changeRow(change)); });
    if (changes.length) { changeBox.appendChild(changeList); }  // with none, the sentence above says the scope is as approved
    if (isObject(PLAN.clipped) && !preview) { changeBox.appendChild(clippedNote(false)); }
    // A preview was never approved, so nothing has changed since: no fold, no scope sentence and no chart.
    if (!preview) { folds.appendChild(fold("changes", "Changes since approval", changes.length, changeBox)); }
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
    // A preview has no changes fold, so what the page left out is said at the chapter's end.
    if (isObject(PLAN.clipped) && preview) { nodes.push(clippedNote(true)); }
    inner.replaceChildren.apply(inner, nodes);
  }

  /** In a stop's panel: where its unit stands, in the plan's own words, and separately a way to it. */
  function planStop(doc, subject, stated) {
    if (!plan) { return null; }
    var unit = planUnits(plan.units).filter(function (entry) { return entry.subject === subject; })[0];
    if (!unit) { return null; }
    var proof = planProofs(doc, [unit])[subject];
    var removed = unit.removed === true;
    var box = add(el("div", "d-plan-row" + (!removed && proof.proven ? " proven" : removed ? " removed" : "") +
      (!removed && proof.next && proofStuck(proof.next, proof) ? " stuck" : "")),
      // A story whose card says where it stands leaves its proofs to the plan, behind the button.
      stated && !removed && !proof.proven ? null : el("span", "pu-standing", removed ? "Removed from the plan" : unitStanding(proof)));
    if (!removed && proof.next && !stated) { box.appendChild(el("span", "d-plan-next", proofSaid(proof.next, proof, Boolean(closedEntry(doc))))); }
    var go = withKey(el("button", "d-plan", "See in the plan"), "plan-unit", subject);
    go.type = "button";
    go.setAttribute("data-plan-unit", subject);
    go.addEventListener("click", function (event) {
      event.stopPropagation();
      var row = !proof.proven && unit.removed !== true ? planRows[subject] : null;
      if (!row) {
        // The row is in the units fold, which page.go opens, saved, on the way to it.
        planAsApproved = false;
        page.redraw(function () { drawPlan(shown); }, "reader");
        row = planRows["all:" + subject];
      }
      if (row) {
        page.go({ chapter: "plan", node: row }, "link");
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
  // The reader's depth, saved under its own key, and the filter, kept for the visit.
  var depthPick = page.state(DEPTH_KEY, { saved: true });
  var filterPick = page.state("story:filter");
  function storyFilterNow() { var picked = filterPick.get(); return typeof picked === "string" ? picked : "all"; }
  function storyOf(doc) {
    var story = isObject(doc.story) ? doc.story : null;
    return story && Array.isArray(story.chapters) && Array.isArray(story.beats) && Array.isArray(story.threads) && isObject(story.digest) ? story : null;
  }
  // The reader's depth: theirs once chosen, else Story while the delivery runs and Headlines once it has closed.
  function storyDepth(story) {
    var chosen = depthPick.get();
    if (DEPTHS.some(function (depth) { return depth[0] === chosen; })) { return chosen; }
    return story.state === "closed" ? "headline" : "story";
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
    var commit = withKey(el(href ? "a" : "code", "fact-ref", short(sha)), "commit", sha);
    if (href) {
      commit.setAttribute("href", href);
      commit.setAttribute("target", "_blank");
      commit.setAttribute("rel", "noopener noreferrer");
      commit.setAttribute("aria-label", "Open commit " + short(sha));
    }
    return commit;
  }
  /**
   * The story's words, made readable without changing what they say, through the page's one formatter (richWords, with
   * the run summary): each ticket id they name links to its ticket, so a reader can open it or see its card as anywhere
   * else on the page; a commit opens on the forge, cut short to read; a change request's address shows as its number; a
   * `quoted` name reads as code. The ids are the record's own, the ones the renderer gave addresses for.
   */
  function linked(words) {
    return richWords(text(words), {
      tickets: Object.keys(LINKS.issues || {}),
      ticket: function (id) { return issue(id); },
      // A commit is named in full or cut short; a short one links only when it begins exactly one commit the page knows.
      commit: function (sha) {
        var named = sha.length === 40 ? [sha] : Object.keys(LINKS.commits || {}).filter(function (known) { return known.indexOf(sha) === 0; });
        return named.length === 1 ? commitRef(named[0]) : null;
      },
      change: function (address) { return changeTarget(address) ? changeLink(address) : null; },
    });
  }
  /** A reference to a file's lines, as the renderer linked it at the commit the words were written about, or null. */
  function fileHref(sha, ref) {
    return typeof LINKS.files[sha + " " + ref] === "string" ? LINKS.files[sha + " " + ref] : null;
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
    story.beats.forEach(function (beat) { if (beatShown(beat, depth, storyFilterNow())) { present[text(beat.tone)] = true; } });
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
    if (!story.beats.some(isObject)) { drawStoryAhead(inner, story); prev.story = Object.create(null); return; }
    var depth = storyDepth(story);
    var seen = Object.create(null);
    story.beats.forEach(function (beat) { if (isObject(beat)) { seen[beatKey(beat)] = true; } });
    var storyFilter = storyFilterNow();
    var switches = add(el("div", "story-bar"), choice("Depth", DEPTHS, depth, function (picked) { depthPick.set(picked); }, "depth"),
      choice("Show", FILTERS, storyFilter, function (picked) { filterPick.set(picked); }, "tab"));
    var beats = Object.create(null);
    story.beats.forEach(function (beat) { if (isObject(beat)) { beats[text(beat.id)] = beat; } });
    // The setbacks, each told once in the ledger above the chapters, by row id.
    var ledger = Array.isArray(story.ledger) ? story.ledger.filter(function (row) { return isObject(row) && text(row.id); }) : null;
    var rows = Object.create(null);
    (ledger || []).forEach(function (row) { rows[text(row.id)] = row; });
    var ol = el("ol", "story");
    story.chapters.slice().reverse().forEach(function (chapter) {
      if (!isObject(chapter) || !isObject(chapter.words)) { return; }
      var shownBeats = list(chapter.beats).map(function (name) { return beats[text(name)]; })
        .filter(function (beat) { return beatShown(beat, depth, storyFilter); }).reverse();
      // With a ledger, a chapter names each setback that opened or ended in it, once, linked to its row; a story told
      // before the ledger keeps the standing lines it carried.
      var setbacks = ledger ? list(chapter.setbacks).map(function (name) { return rows[text(name)]; }).filter(Boolean) : [];
      var standing = ledger ? [] : list(chapter.words.standing).filter(isObject);
      var showsSetbacks = storyFilter === "all" || storyFilter === "problems";
      if (storyFilter !== "all" && !shownBeats.length && !(storyFilter === "problems" && (standing.length || setbacks.length)) && !(storyFilter === "landed" && chapter.form === "landing")) { return; }
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
      if (showsSetbacks) { setbacks.forEach(function (row) { li.appendChild(setbackLine(row, chapter)); }); }
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
    var say = ledger ? ledgerSay(ledger, story.digest) : text(digest.line);
    add(inner, el("div", "kicker", "What happened"), el("h2", "title", text(digest.head)),
      say ? el("p", "say", say) : null, storyLegend(story, depth), switches,
      ledger && ledger.length && (storyFilter === "all" || storyFilter === "problems") ? setbackLedger(ledger) : null, ol);
    // Depth changes density in place: the chapter eases from the height it had, on the shared fold's motion.
    runEaseHeight(inner, from);
  }
  /**
   * Before anything has happened: no switches over an empty list, just what the chapter will tell and the marks it
   * will tell it with, so the first landing, decision or setback reads at once when it comes.
   */
  function drawStoryAhead(inner, story) {
    var started = isObject(story.digest) && text(story.digest.startedAt);
    var key = add.apply(null, [el("ul", "ahead")].concat(
      [["landed", "Landings", "Each piece as it merges, and how long it took."],
       ["decision", "Decisions", "Each call the lead makes, and why."],
       ["problem", "Setbacks", "What stopped work, until when, and what it cost."]].map(function (kind) {
        return add(el("li", "is-" + kind[0]), toneMark(kind[0]), add(el("div", "ahead-words"), el("b", null, kind[1]), el("span", null, kind[2])));
      })));
    inner.replaceChildren(el("div", "kicker", "What happened"), el("h2", "title", "Nothing has happened yet."),
      el("p", "say", started ? "The story is told here as it unfolds, newest first." : "The story starts with the first lane, and is told here as it unfolds, newest first."),
      key);
  }
  function beatKey(beat) { return text(beat.form) + ":" + list(beat.entries).join(","); }
  var NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
  function numberWord(count) { return count < NUMBER_WORDS.length ? NUMBER_WORDS[count] : String(count); }
  function capital(words) { return words.charAt(0).toUpperCase() + words.slice(1); }
  /**
   * What happened in one line, read from the ledger: whether anything is stopped now, how many setbacks are over and what
   * they cost, then the lead's decisions. Copies of one setback are one row, so one setback is counted once.
   */
  function ledgerSay(ledger, digest) {
    var open = ledger.filter(function (row) { return row.state === "open"; });
    var over = ledger.filter(function (row) { return row.state !== "open"; });
    var parts = [];
    if (!ledger.length) { parts.push("No setbacks."); }
    else if (open.length) { parts.push(capital(numberWord(open.length)) + (open.length === 1 ? " setback stands now." : " setbacks stand now.")); }
    else { parts.push("Nothing is stopped now."); }
    if (over.length) {
      // What the ended setbacks cost together: the question the line answers, with no figure to match to a row.
      var cost = over.reduce(function (sum, row) { return sum + (typeof row.cost === "number" && row.cost > 0 ? row.cost : 0); }, 0);
      parts.push(capital(numberWord(over.length)) + (over.length === 1 ? " is over" : " are over") +
        (cost > 0 ? ", costing " + duration(cost) : "") + ".");
    }
    var decisions = typeof digest.decisions === "number" ? digest.decisions : 0;
    if (decisions) { parts.push("The lead made " + numberWord(decisions) + (decisions === 1 ? " decision." : " decisions.")); }
    return parts.join(" ");
  }
  /** When a row's setbacks happened: one instant, or the first and the last of several. */
  function rowSpan(row) {
    var first = clock(row.firstOpenedAt) || "a time unknown";
    return row.count > 1 && text(row.lastOpenedAt) && row.lastOpenedAt !== row.firstOpenedAt ? first + "–" + (clock(row.lastOpenedAt) || "a time unknown") : first;
  }
  /**
   * The ledger: every setback once, grouped by story and reason, the ones still standing first, tinted, with since when
   * and who they wait on; the ended ones after, muted, with what ended them, when, and what they cost. Each row can take
   * focus, and a chapter's line about it leads to it.
   */
  function setbackLedger(ledger) {
    var box = add(el("section", "setback-ledger"), el("h3", "ledger-title", "Setbacks"));
    box.setAttribute("aria-label", "Setbacks");
    var ul = el("ul", "ledger-rows");
    ledger.forEach(function (row) {
      var open = row.state === "open";
      var li = el("li", "ledger-row " + (open ? "is-open" : "is-closed"));
      li.id = "setback-" + text(row.id);
      li.setAttribute("data-row", text(row.id));
      li.setAttribute("tabindex", "0");
      withKey(li, "setback", text(row.id));
      var words = isObject(row.words) ? row.words : {};
      var what = add(el("div", "ledger-what"), add.apply(null, [el("b")].concat(linked(words.head))), el("span", "ledger-line", text(words.line)));
      var when = open
        ? "since " + (clock(row.since) || "a time unknown") + (text(row.waitingOn) ? " · waits on " + text(row.waitingOn) : "")
        : "ended " + (clock(row.endedAt) || "at a time unknown") + (typeof row.cost === "number" && row.cost > 0 ? " · cost " + duration(row.cost) : "");
      var meta = add(el("div", "ledger-when"), el("span", "ledger-span", rowSpan(row)), el("span", "ledger-state", when));
      add(li, glyph("stop"), what, meta);
      ul.appendChild(li);
    });
    return add(box, ul);
  }
  /**
   * A chapter's line about one setback: what it was, and whether it opened here, ended here, or still stands, linked to
   * its row in the ledger. Following the link moves focus to the row.
   */
  function setbackLine(row, chapter) {
    var words = isObject(row.words) ? row.words : {};
    var chapters = isObject(row.chapters) ? row.chapters : {};
    var here = text(chapter.id);
    var state = row.state === "open" ? "still stands" : chapters.ended === here && chapters.opened === here ? "over" :
      chapters.ended === here ? "ended here" : "opened here, ended later";
    var link = withKey(el("a", "setback-link", text(words.head).replace(/\.$/, "") + " · " + state), "setback-link", text(row.id) + ":" + here);
    link.setAttribute("href", "#setback-" + text(row.id));
    link.addEventListener("click", function (event) {
      var target = document.getElementById("setback-" + text(row.id));
      if (!target) { return; }
      event.preventDefault();
      page.go({ item: target.id }, "link");
      target.focus({ preventScroll: true });
    });
    return add(el("p", "chapter-setback" + (row.state === "open" ? " is-open" : "")), glyph("stop"), link);
  }
  /** A small group of buttons choosing one of its options, drawn again on a pick. */
  function choice(label, options, current, pick, data) {
    var group = el("div", "tabs" + (data === "depth" ? " depth" : " filter"));
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", label);
    options.forEach(function (option) {
      var button = withKey(el("button", null, option[1]), data, option[0]);
      button.type = "button";
      button.setAttribute("aria-pressed", String(current === option[0]));
      button.setAttribute("data-" + data, option[0]);
      // Drawn again through the page's one redraw transaction, which brings focus back to the button by its key.
      button.addEventListener("click", function () { pick(option[0]); page.redraw(function () { drawHappened(shown); }, "reader"); });
      group.appendChild(button);
    });
    return group;
  }

  /* Across repositories */
  /** A story a sibling delivery's last read listed, by its subject, or undefined. */
  function siblingStory(doc, id) {
    var found;
    list(isObject(doc.across) ? doc.across.groups : null).filter(isObject).forEach(function (group) {
      found = found || list(group.stories).filter(function (story) { return isObject(story) && story.subject === id; })[0];
    });
    return found;
  }
  /**
   * The epic's other repositories, a group each, folded shut: each sibling delivery's name, linked to its live view, since
   * when it has shown no change or why it can't be read now, and its stories as its last kept read showed them. The chapter is made the first
   * time a record names a sibling, after What's left, and stays hidden while none is named.
   */
  function drawAcross(doc) {
    var groups = isObject(doc.across) ? list(doc.across.groups).filter(function (group) { return isObject(group) && text(group.name); }) : [];
    if (!groups.length && !parts.across) { return; }
    var section = parts.across ? parts.across.parentNode : chapterOf(["across", "Across repositories"], PLAN ? "plan" : "happened");
    var link = rail.querySelector('a[href="#across"]');
    section.classList.toggle("is-absent", !groups.length);
    section.hidden = !groups.length || (PAGED && section.id !== currentPage);
    if (link) { link.hidden = !groups.length; }
    if (!groups.length) {
      parts.across.replaceChildren();
      // A reader on the chapter as it empties turns to the first page rather than an empty one.
      if (currentPage === "across") { page.go({ chapter: chapterIds()[0] }, "address"); }
      return;
    }
    // A group of another epic delivers nothing of this one: it is named after the epic's own, under Also linked.
    var others = groups.filter(function (group) { return group.lastOutcome === "other-epic"; });
    groups = groups.filter(function (group) { return group.lastOutcome !== "other-epic"; });
    var unread = groups.filter(function (group) { return group.fresh !== true; }).length;
    var nodes = [el("div", "kicker", "Across repositories"),
      el("h2", "title", (groups.length === 0 ? "No other repository delivers this epic." : groups.length + (groups.length === 1
        ? " other repository delivers this epic." : " other repositories deliver this epic.")) + (unread ? " " + unread + " can't be read now." : ""))];
    var headOf = function (group) {
      var name = typeof group.link === "string" && /^https?:\/\//.test(group.link) ? el("a", "across-name", text(group.name)) : el("span", "across-name", text(group.name));
      if (name.tagName === "A") {
        name.setAttribute("href", group.link);
        name.setAttribute("target", "_blank");
        name.setAttribute("rel", "noopener noreferrer");
        name.setAttribute("aria-label", "Open " + text(group.name) + "'s live view");
      }
      // The state tool's time: the last read that changed a fresh sibling, or since when any other has been as it is.
      var since = text(group.since);
      var when = since ? el("time", null, spoken(since) || since) : null;
      if (when) { when.setAttribute("datetime", since); when.setAttribute("title", since); }
      var reason = text(group.reason);
      var said = group.fresh === true ? add(el("span", "across-read"), "No change since ", when)
        : reason && when ? add(el("span", "across-read is-unread"), reason.charAt(0).toUpperCase() + reason.slice(1) + " since ", when,
          text(group.readAt) ? null : " · never read")
        : el("span", "across-read is-unread", "Never read");
      return add(el("div", "across-head" + (group.fresh === true ? "" : " is-unread")), name, said);
    };
    if (groups.length) {
      var fold = el("details", "across-fold");
      add(fold, el("summary", null, groups.map(function (group) { return text(group.name); }).join(" · ")));
      groups.forEach(function (group) {
        var ul = el("ul", "parked across");
        list(group.stories).filter(function (story) { return isObject(story) && text(story.subject); }).forEach(function (story) {
          var row = add(el("li", story.landed === true ? "is-landed" : ""), add(el("span", "p"), issue(text(story.subject))),
            text(story.title) || text(story.subject));
          add(row, el("span", "p", stageWords(story)));
          ul.appendChild(row);
        });
        add(fold, add(el("div", "across-group"), headOf(group), ul.firstChild ? ul : null));
      });
      // Shut by default, as Elsewhere is; a reader who opened it keeps it open through a redraw.
      page.fold(fold, { key: "across" });
      nodes.push(fold);
    }
    if (others.length) {
      var also = add(el("div", "across-also"), el("span", "across-also-head", "Also linked"));
      others.forEach(function (group) { also.appendChild(headOf(group)); });
      nodes.push(also);
    }
    parts.across.replaceChildren.apply(parts.across, nodes);
  }

  /* Shared records: who writes it, who holds what, and the feedback on its plan */
  // A shared record's meta.collab, or none: a record nobody shares draws neither chapter, and is drawn as it always was.
  function collabOf(doc) {
    var meta = isObject(doc.state) && isObject(doc.state.meta) ? doc.state.meta : null;
    return meta && isObject(meta.collab) ? meta.collab : null;
  }
  function entriesOf(doc) { return list(doc.journal).filter(function (entry) { return isObject(entry) && isObject(entry.data); }); }
  /** Each seat's latest label, from the record's seats and its seat entries; a label is only shown, never read. */
  function seatLabels(doc, collab) {
    var labels = Object.create(null);
    var seats = isObject(collab.seats) ? collab.seats : {};
    Object.keys(seats).forEach(function (seat) { if (isObject(seats[seat]) && text(seats[seat].label)) { labels[seat] = text(seats[seat].label); } });
    entriesOf(doc).forEach(function (entry) {
      if (/^seat\./.test(text(entry.kind)) && text(entry.data.seat) && text(entry.data.label)) { labels[entry.data.seat] = entry.data.label; }
    });
    return labels;
  }
  function seatName(labels, seat) { return labels[seat] || text(seat) || "a seat"; }
  /**
   * D6's scopes as the journal holds them: the record's first seat holds every scope until its holder passes one on, a
   * plan section nobody holds is the delivery holder's, and a transfer counts only when the scope's holder made it.
   */
  function scopeRegister(doc) {
    var first = null, held = Object.create(null), passed = [], last = Object.create(null);
    entriesOf(doc).forEach(function (entry) {
      var origin = isObject(entry.origin) ? entry.origin : null;
      if (!origin) { return; }
      if (entry.kind === "record.started" && first === null) { first = text(origin.seat); return; }
      if (entry.kind !== "scope.transferred") { return; }
      var scope = text(entry.data.scope);
      var holder = scope in held ? held[scope] : /^plan:/.test(scope) ? (held.delivery || first) : first;
      if (!scope || holder !== text(origin.seat) || holder !== text(entry.data.from)) { return; }
      if (!(scope in held) && scope !== "delivery" && scope !== "queue") { passed.push(scope); }
      held[scope] = text(entry.data.to);
      last[scope] = entry;
    });
    return { first: first, held: held, passed: passed, last: last };
  }
  /** Who holds a scope: its holder once passed on, the first seat until then; a plan section nobody holds is the
   * delivery holder's, and `seat:<id>` is always `<id>`'s. */
  function holderOf(register, scope) {
    if (/^seat:./.test(scope)) { return scope.slice(5); }
    if (scope in register.held) { return register.held[scope]; }
    return /^plan:/.test(scope) ? holderOf(register, "delivery") : register.first;
  }
  /* Whose a call is, on a shared record. The pass stamps each Needs you item with its scope and the seat holding it, and
     words it three ways; the page only checks the stamp against the journal and picks the words for its reader. */
  var registerSeen = { doc: null, register: null };
  function registerOf(doc) {
    if (registerSeen.doc !== doc) { registerSeen = { doc: doc, register: scopeRegister(doc) }; }
    return registerSeen.register;
  }
  /** The seat an item's pass stamped it with, and whether the journal has since given its scope to another seat. */
  function needSeat(doc, item) {
    var seat = text(item.seat), scope = text(item.scope);
    if (!collabOf(doc) || !seat) { return { seat: null, outrun: false }; }
    return { seat: seat, outrun: Boolean(scope) && holderOf(registerOf(doc), scope) !== seat };
  }
  /** The seat this page is drawn for, when it is one of the record's seats; a page for any other seat has none. */
  function viewerOf(doc) {
    var collab = collabOf(doc);
    return collab && VIEWER && isObject(collab.seats) && isObject(collab.seats[VIEWER]) ? VIEWER : null;
  }
  /** The reader's own call. Unshared, every person's call is; shared, only one whose stamped seat is this page's and
   * still holds it. A page with no seat has none. */
  function yours(doc, item) {
    if (!collabOf(doc)) { return item.who === "operator"; }
    if (item.who !== "operator" || !viewerOf(doc)) { return false; }
    var owner = needSeat(doc, item);
    return owner.seat === viewerOf(doc) && !owner.outrun;
  }
  /** Another seat's person's call, still theirs: that seat's label; otherwise none. */
  function theirs(doc, item) {
    var collab = collabOf(doc);
    if (!collab || item.who !== "operator") { return null; }
    var owner = needSeat(doc, item);
    return owner.seat && !owner.outrun && owner.seat !== viewerOf(doc) ? seatName(seatLabels(doc, collab), owner.seat) : null;
  }
  /** The words an item reads on this page: the pass's own for its seat, every owner by label with no seat, and by role
   * when the stamp can't be trusted. */
  function needSaid(doc, item) {
    var pick = function (name) { return isObject(item[name]) ? item[name] : null; };
    if (!collabOf(doc)) { return pick("said") || {}; }
    var owner = needSeat(doc, item);
    if (!owner.seat || owner.outrun) { return pick("saidRole") || pick("saidAll") || pick("said") || {}; }
    return (viewerOf(doc) ? pick("said") : pick("saidAll") || pick("saidRole")) || pick("said") || {};
  }
  /** Whose the rejoin's person words are, the delivery holder's: null for the reader's own, else the holder's label. */
  function rejoinOwner(doc) {
    var collab = collabOf(doc);
    if (!collab) { return null; }
    var holder = holderOf(registerOf(doc), "delivery");
    return holder && holder === viewerOf(doc) ? null : seatName(seatLabels(doc, collab), holder);
  }
  /** "your" for the reader's own merge, "Ama's" for another's. */
  function rejoinWhose(doc) { var owner = rejoinOwner(doc); return owner ? owner + "'s" : "your"; }
  /** The rejoin's marks with its person mark given to the delivery holder when that isn't the reader. */
  function rejoinMarksFor(doc, marks) {
    var owner = rejoinOwner(doc);
    if (owner) { marks.forEach(function (made) { if (made.who === "you") { made.who = "seat"; made.name = owner; } }); }
    return marks;
  }
  /** Whose page this is on a shared record: the reader's seat and what it holds, then who else writes it; with no seat,
   * every seat that writes it. Nothing on a record nobody shares. */
  function seatLine(doc) {
    var collab = collabOf(doc);
    if (!collab) { return null; }
    var labels = seatLabels(doc, collab), register = registerOf(doc);
    var seats = isObject(collab.seats) ? collab.seats : {};
    var admitted = Object.keys(seats).filter(function (seat) { return isObject(seats[seat]) && seats[seat].admitted === true; });
    var box = el("div", "seat-line");
    var viewer = viewerOf(doc);
    if (viewer) {
      var held = ["delivery", "queue"].filter(function (scope) { return holderOf(register, scope) === viewer; })
        .concat(list(doc.state.lanes).filter(function (lane) { return isObject(lane) && text(lane.id) && holderOf(register, "lane:" + lane.id) === viewer; })
          .map(function (lane) { return laneLabel(lane.id).toLowerCase(); }));
      add(box, el("span", "seat-chip", [seatName(labels, viewer)].concat(held.length ? ["holds " + held[0]].concat(held.slice(1)) : []).join(" · ")));
      var others = admitted.filter(function (seat) { return seat !== viewer; }).map(function (seat) { return seatName(labels, seat); });
      if (others.length) { add(box, el("span", "seat-shared", "Shared with " + spokenList(others))); }
    } else {
      add(box, el("span", "seat-shared", "Shared by " + spokenList(admitted.map(function (seat) { return seatName(labels, seat); }))));
    }
    return box;
  }
  /** A queue handed on that its new holder has not yet taken up: no entry of that seat has seen the transfer. */
  function queueMoving(doc, register) {
    var moved = register.last.queue;
    if (!moved) { return null; }
    var to = text(moved.data.to), from = text(moved.origin.seat), seq = moved.origin.seq;
    var taken = entriesOf(doc).some(function (entry) {
      var origin = isObject(entry.origin) ? entry.origin : null;
      return origin && text(origin.seat) === to && isObject(origin.seen) && typeof origin.seen[from] === "number" && origin.seen[from] >= seq;
    });
    return taken ? null : to;
  }
  var PEER_HELD = table({ generation_differs: "held: generation differs", slice_invalid: "held: its updates don't fit this record" });
  /**
   * A peer as the sync loop last read it: "Ben as of 4s" or "Ben held: generation differs". A split is never a peer's:
   * it is the record's own, said at the chapter's head.
   */
  function peerWords(name, peer) {
    var reason = text(peer.heldReason);
    if (reason) { return name + " " + (PEER_HELD[reason] || "held"); }
    var at = seconds(text(peer.fetchedAt));
    return at === null ? name + " not read yet" : name + " as of " + duration(Math.max(0, Date.now() / 1000 - at));
  }
  /** A chapter made the first time a record is shared, before `before`, and hidden while the record isn't. */
  function sharedChapter(id, name, before, shared) {
    if (!shared && !parts[id]) { return null; }
    var section = parts[id] ? parts[id].parentNode : chapterOf([id, name], before);
    var link = rail.querySelector('a[href="#' + id + '"]');
    section.classList.toggle("is-absent", !shared);
    section.hidden = !shared || (PAGED && section.id !== currentPage);
    if (link) { link.hidden = !shared; }
    if (!shared) {
      parts[id].replaceChildren();
      if (currentPage === id) { page.go({ chapter: chapterIds()[0] }, "address"); }
      return null;
    }
    return parts[id];
  }
  /** Seats: who writes this shared record, who holds each scope, how fresh each peer is, and a queue moving between seats. */
  function drawSeats(doc) {
    var collab = collabOf(doc);
    var inner = sharedChapter("seats", "Seats", "left", collab);
    if (!inner) { return; }
    var labels = seatLabels(doc, collab);
    var register = scopeRegister(doc);
    var seats = isObject(collab.seats) ? collab.seats : {};
    var ids = Object.keys(seats);
    var admitted = ids.filter(function (seat) { return isObject(seats[seat]) && seats[seat].admitted === true; });
    var who = el("ul", "parked seats-list");
    ids.forEach(function (seat) {
      var held = isObject(seats[seat]) ? seats[seat] : {};
      var role = held.admitted !== true ? "waiting to be admitted" : held.approver === true ? "endorses the plan" : "writes, doesn't endorse";
      who.appendChild(add(el("li", "seat-row"), add(el("span", "p"), el("span", "seat-id", text(seat))), seatName(labels, seat),
        el("span", "p seat-role", role)));
    });
    var moving = queueMoving(doc, register);
    var scopes = el("ul", "parked scopes-list");
    var row = function (scope, holder, note) {
      scopes.appendChild(add(el("li", "scope-row"), add(el("span", "p"), el("span", "scope-name", scope)), seatName(labels, holder),
        note ? el("span", "p scope-note", note) : null));
    };
    row("delivery", holderOf(register, "delivery"), null);
    row("queue", holderOf(register, "queue"), moving ? "queue moving to " + seatName(labels, moving) : null);
    list(doc.state.lanes).filter(function (lane) { return isObject(lane) && text(lane.id); }).forEach(function (lane) {
      if (register.passed.indexOf("lane:" + lane.id) < 0) { row("lane:" + lane.id, holderOf(register, "lane:" + lane.id), null); }
    });
    register.passed.forEach(function (scope) { row(scope, register.held[scope], null); });
    var peers = isObject(collab.peers) ? collab.peers : {};
    var fresh = el("ul", "parked peers-list");
    Object.keys(peers).sort().forEach(function (seat) {
      if (isObject(peers[seat])) { fresh.appendChild(add(el("li", "peer-row"), peerWords(seatName(labels, seat), peers[seat]))); }
    });
    var nodes = [el("div", "kicker", "Seats"),
      el("h2", "title", admitted.length + (admitted.length === 1 ? " seat writes" : " seats write") + " this delivery" +
        (moving ? ", and its queue is moving to " + seatName(labels, moving) + "." : "."))];
    if (text(collab.heldReason) === "seat_split") {
      nodes.push(el("p", "say seats-split", "This checkout's seat split from its chain: it writes nothing more here."));
    }
    nodes.push(add(el("div", "seats-group"), el("h3", "seats-head", "Who writes"), who),
      add(el("div", "seats-group"), el("h3", "seats-head", "Who holds what"), scopes,
        el("p", "p seats-note", "Every other plan section is " + seatName(labels, holderOf(register, "delivery")) + "'s, as the delivery's holder.")));
    if (fresh.firstChild) { nodes.push(add(el("div", "seats-group"), el("h3", "seats-head", "Peers"), fresh)); }
    inner.replaceChildren.apply(inner, nodes);
  }
  var FEEDBACK_SETTLED = table({ accepted: "Accepted", declined: "Declined", answered: "Answered" });
  /** Feedback on the plan, a thread on each section it is on: what was raised, by whom, and how its holder settled it. */
  function drawFeedback(doc) {
    var collab = collabOf(doc);
    var raised = collab ? entriesOf(doc).filter(function (entry) { return entry.kind === "feedback.raised" && text(entry.data.anchor); }) : [];
    var inner = sharedChapter("feedback", "Feedback", "happened", collab && raised.length ? collab : null);
    if (!inner) { return; }
    var labels = seatLabels(doc, collab);
    var settled = Object.create(null);
    entriesOf(doc).forEach(function (entry) {
      if (entry.kind === "feedback.resolved") { list(entry.answers).forEach(function (id) { settled[id] = entry; }); }
    });
    var writer = function (entry) { return seatName(labels, isObject(entry.origin) ? text(entry.origin.seat) : ""); };
    var anchors = [];
    raised.forEach(function (entry) { if (anchors.indexOf(entry.data.anchor) < 0) { anchors.push(entry.data.anchor); } });
    var open = raised.filter(function (entry) { return !settled[entry.id]; });
    var blocking = open.filter(function (entry) { return entry.data.blocking === true; });
    var nodes = [el("div", "kicker", "Feedback"),
      el("h2", "title", (open.length ? open.length + " open" : "None open") + (blocking.length ? ", " + blocking.length + " blocking" : "") +
        " · " + raised.length + " raised on " + anchors.length + (anchors.length === 1 ? " section." : " sections."))];
    anchors.forEach(function (anchor) {
      var thread = el("ul", "parked feedback-thread");
      raised.filter(function (entry) { return entry.data.anchor === anchor; }).forEach(function (entry) {
        var answer = settled[entry.id];
        var state = answer ? (FEEDBACK_SETTLED[text(answer.data.outcome)] || "Settled") + " by " + writer(answer) +
          (text(answer.data.reason) ? ": " + answer.data.reason : "") : entry.data.blocking === true ? "Open, blocking" : "Open";
        thread.appendChild(add(el("li", "feedback-row" + (answer ? " is-settled" : entry.data.blocking === true ? " is-blocking" : "")),
          el("span", "p", writer(entry)), text(entry.data.text), el("span", "p feedback-state", state)));
      });
      nodes.push(add(el("div", "feedback-group"), el("h3", "feedback-anchor", anchor), thread));
    });
    inner.replaceChildren.apply(inner, nodes);
  }

  /* 6. Parked */
  function asideChevron() {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "aside-chevron");
    svg.innerHTML = '<path d="M6 9l6 6l6 -6"/>';
    return svg;
  }
  // Only rows whose text runs past two lines become openable: each is measured clamped, whether or not it is open.
  function markClamped(listNode) {
    Array.prototype.forEach.call(listNode.children, function (row) {
      var head = row.firstChild;
      row.classList.add("measuring");
      var clamps = head.scrollHeight > head.clientHeight + 1;
      row.classList.remove("measuring");
      row.classList.toggle("clamps", clamps);
      if (clamps) {
        row.tabIndex = 0;
        row.setAttribute("role", "button");
        if (!row.hasAttribute("aria-expanded")) { row.setAttribute("aria-expanded", "false"); }
      } else {
        row.removeAttribute("tabindex");
        row.removeAttribute("role");
        row.removeAttribute("aria-expanded");
      }
    });
  }
  window.addEventListener("resize", function () {
    Array.prototype.forEach.call(document.querySelectorAll("ol.aside"), markClamped);
  });

  function drawParked(doc) {
    var inner = parts.parked;
    var items = list(doc.state.followups).filter(isObject);
    var nodes = [el("div", "kicker", "Parked for later"),
      el("h2", "title", items.length ? items.length + (items.length === 1 ? " thing found and set aside." : " things found and set aside.") : "Nothing parked yet.")];
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
    } else if (!closedEntry(doc)) {
      nodes.push(el("p", "say", "What a lane finds outside its story is set aside here, not added to the work."));
      // What the plan itself leaves for later, so the page says what is already out of this delivery.
      var aside = plan ? list(plan.outOfScope).map(text).filter(Boolean) : [];
      if (aside.length) {
        var label = el("a", "aside-label", "Set aside by the plan");
        label.setAttribute("href", "#plan");
        var asideList = el("ol", "aside");
        aside.forEach(function (words) {
          // The full text, clamped to two lines; a row that overflows opens in place to show all of it.
          // A row is a disclosure on the page's one fold, keyed by its words, which are what the plan names it by.
          var row = add(el("li"), el("span", "aside-head", words), asideChevron());
          // Focusable as it is drawn, so focus can come back to it through a redraw; a row that turns out not to clamp
          // loses it once measured.
          row.tabIndex = 0;
          page.fold({ button: row }, { key: "aside:" + words, can: function () { return row.classList.contains("clamps"); } });
          asideList.appendChild(row);
        });
        requestAnimationFrame(function () { markClamped(asideList); });
        nodes.push(add(el("div", "aside-box"), add(el("div", "aside-top"), label, el("span", "aside-count", String(aside.length))), asideList));
      }
    }
    var elsewhere = elsewhereFold(doc);
    if (elsewhere) { nodes.push(elsewhere); }
    inner.replaceChildren.apply(inner, nodes);
  }

  /**
   * The parent's children a scoped tracker snapshot put outside this delivery: another repository's work, folded shut,
   * each with its tracker status and the value that places it elsewhere. The sweep never takes them in.
   */
  function elsewhereFold(doc) {
    // A child a sibling delivery holds is drawn in that sibling's group instead.
    var held = isObject(doc.across) && isObject(doc.across.held) ? doc.across.held : {};
    var items = list(doc.state.outside).filter(function (item) {
      return isObject(item) && text(item.subject) && !Object.prototype.hasOwnProperty.call(held, item.subject);
    });
    if (!items.length) { return null; }
    var meta = isObject(doc.state.meta) ? doc.state.meta : {};
    var parent = text(meta.trackerParent);
    var words = (parent ? "Elsewhere in " + parent : "Elsewhere") + " · " + items.length +
      (items.length === 1 ? " piece another delivery holds" : " pieces other deliveries hold");
    var ul = el("ul", "parked elsewhere");
    items.forEach(function (item) {
      var marks = isObject(item.marks) ? item.marks : {};
      var placed = ["component", "label", "prefix"].map(function (kind) { return list(marks[kind]).filter(text).join(", "); })
        .filter(Boolean).join(" · ");
      var row = add(el("li"), add(el("span", "p"), issue(text(item.subject))), text(item.title) || text(item.subject));
      add(row, el("span", "p", [text(item.status) || text(item.state), placed].filter(Boolean).join(" · ")));
      ul.appendChild(row);
    });
    var fold = add(el("details", "elsewhere-fold"), el("summary", null, words), ul);
    // Shut by default; a reader who opened it keeps it open through a redraw.
    return page.fold(fold, { key: "elsewhere" });
  }

  function usable(doc) { return isObject(doc) && isObject(doc.state); }

  function draw(doc) {
    if (!usable(doc)) {
      parts.now.replaceChildren(el("div", "kicker", pageTitle()), el("h1", "title", "The delivery record could not be read."));
      Object.keys(parts).filter(function (name) { return name !== "now"; }).forEach(function (name) { parts[name].replaceChildren(); });
      return;
    }
    // A redraw can remove the id under the pointer without a mouseleave, so its card goes too.
    hideCard();
    shown = doc;
    var all = pieces(doc);
    drawNow(doc, all);
    drawLine(doc, all);
    drawWho(doc, all);
    drawLeft(doc, all);
    drawAcross(doc);
    drawSeats(doc);
    if (parts.plan) { drawPlan(doc); }
    drawFeedback(doc);
    drawHappened(doc);
    drawParked(doc);
    dropTurned();
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
  // A record that fails to draw leaves the last good drawing in place and says so. Every redraw goes through the page's
  // one redraw transaction (page.js), which keeps the reader's place; `why` names what asked for it.
  function safely(doc, why) {
    page.redraw(function () {
      try { draw(doc); lastGood = doc; } catch (error) {
        if (lastGood && doc !== lastGood) {
          if (connection === "live") { connection = "failed"; }
          try { draw(lastGood); } catch (again) { /* the last drawing stays */ }
        }
      }
      // Every draw keeps the reader on their page; the first one opens the page the address names.
      if (currentPage === null) {
        page.start({ chapters: chapterIds, show: showPage, layout: function () { return PAGED ? "paged" : "scroll"; } });
      } else if (PAGED) { showPage(currentPage); }
    }, why);
  }

  safely(embedded, "first");
  var store = globalThis.deliveryStateStore;
  if (isObject(store)) {
    if (typeof store.onStatus === "function") {
      try {
        // The adapter reports the connection with every pushed record; only a change is drawn.
        store.onStatus(function (live) {
          var next = live ? "live" : "down";
          if (next === connection) { return; }
          connection = next;
          safely(lastGood || shown, "status");
        });
      } catch (error) { /* a store without a status line still serves its pushes and read below */ }
    }
    if (typeof store.onRuns === "function") {
      try { store.onRuns(function () { refreshRuns(0); }); } catch (error) { /* the pushes and the interval still re-read */ }
    }
    var pushed = false;
    if (typeof store.subscribe === "function") {
      try {
        store.subscribe(function (doc) {
          if (!usable(doc)) { return; }
          pushed = true;
          connection = "live";
          app.setAttribute("data-source", "live");
          safely(doc, "push");
          refreshRuns();
        });
      } catch (error) { /* a store that cannot subscribe still serves its read below */ }
    }
    if (typeof store.read === "function") {
      Promise.resolve().then(function () { return store.read(); }).then(function (doc) {
        if (pushed || !usable(doc)) { return; }
        app.setAttribute("data-source", "live");
        if (connection === "saved") { connection = "connected"; }
        safely(doc, "read");
      }, function () {
        if (!pushed) { connection = "unreadable"; safely(lastGood || shown, "read"); }
      });
    }
  }
  // Relative times and running checks follow the page's own clock between writes.
  // Each tick also re-reads the served page: a supervisor's pass rewrites its Needs you list without a record push.
  if (typeof setInterval === "function") { setInterval(function () { refreshRuns(); safely(lastGood || shown, "tick"); }, 30000); }
  // The run line is drawn at the page's width, so a new width redraws it.
  var resizing = null;
  if (typeof addEventListener === "function") {
    var drawnWidth = innerWidth;
    // Only a new width redraws: a phone's address bar showing or hiding changes the height alone.
    addEventListener("resize", function () {
      if (innerWidth === drawnWidth) { return; }
      drawnWidth = innerWidth;
      clearTimeout(resizing);
      resizing = setTimeout(function () { safely(lastGood || shown, "width"); }, 200);
    });
  }
})();
