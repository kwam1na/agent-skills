// The delivery views' page controller: the one owner of the page's mechanics, loaded first by every view. It defines one
// global, `page`, and nothing else. A feature reaches these mechanics through its public calls, never beside them.
var page = (function () {
  "use strict";
  // The reader's last input, pointer or key, read by the motion check: motion the reader's own key press starts is not
  // shown, since a keyboard reader repeats an action too often to wait on it.
  var lastInput = "pointer";
  if (typeof document !== "undefined" && document && typeof document.addEventListener === "function") {
    document.addEventListener("keydown", function () { lastInput = "keyboard"; }, true);
    document.addEventListener("pointerdown", function () { lastInput = "pointer"; }, true);
  }

  /**
   * The one motion check. Reduced motion always refuses. `fromReader` says the reader's own action starts the motion (a
   * scroll, a fold, the panel swap): that motion is refused too while the last input was a key. Live-update motion (a
   * landing tick, a digit flip, a fresh beat) asks with `fromReader` false, so a keyboard reader still sees a push land.
   */
  function motion(fromReader) {
    if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) { return false; }
    return !(fromReader === true && lastInput === "keyboard");
  }

  /**
   * A control's key: its kind and the record's own id for it (`stop:PAY-2358`, `fold:plan:units`), never its position.
   * Two controls share a key only when they stand for the same thing in two places.
   */
  function key(kind, id) {
    if (typeof kind !== "string" || !kind) { throw new TypeError("page.key needs a kind"); }
    if (id === undefined || id === null || String(id) === "") { throw new TypeError("page.key needs the record's id for its " + kind); }
    return kind + ":" + String(id);
  }

  // The redraw transaction. A page that rebuilds itself (a tick, a new width, a pushed record) loses the reader's place:
  // a browser that lays the page out mid-draw, while a part stands short, clamps the window to what it then holds. The
  // place is kept here, in one transaction around the draw, never in each caller.
  // The stable keys a place is anchored by: what the record names, never a position on the page.
  var ANCHOR_KEYS = ["data-key", "id", "data-subject", "data-ticket", "data-fold"];
  var drawnThisFrame = false;
  var waiting = null;
  // What a redraw is for, and whether it draws at once: something new to say (the page's first record, one read or one
  // pushed, or a new connection status) is shown at once and makes a merged redraw still waiting needless; a redraw of
  // what is already said (the clock, a new width, a refresh) merges with the others asked for in its frame.
  var AT_ONCE = { first: true, read: true, push: true, status: true, reader: true };

  function root() { return document.getElementById("app") || document.body; }
  // An element's mark: its first stable key, with its tag and first class, so a stop and the tick for the same story,
  // which share a key, are told apart. A mark that more than one element carries (a ticket named twice in the feed) can't
  // say which one the reader was on, so it never anchors.
  function markOf(node) {
    for (var i = 0; i < ANCHOR_KEYS.length; i += 1) {
      var value = node.getAttribute(ANCHOR_KEYS[i]);
      if (value) { return { name: ANCHOR_KEYS[i], value: value, tag: node.tagName, kind: firstClass(node) }; }
    }
    return null;
  }
  function firstClass(node) {
    var names = node.getAttribute("class");
    return names ? names.trim().split(/\s+/)[0] : "";
  }
  // Matched by attribute value, never by a selector built from record text.
  function marked(within, mark) {
    return Array.prototype.filter.call(within.querySelectorAll("[" + mark.name + "]"), function (node) {
      return node.getAttribute(mark.name) === mark.value && node.tagName === mark.tag && firstClass(node) === mark.kind;
    });
  }
  function shown(box) { return box.bottom > box.top; }
  /**
   * The reader's place: the elements at the top of the window that only one element on the page is marked as, each with
   * where it stood. First every one across the window's top edge, deepest first, then the first below that edge, each by
   * its top; then, when none of those is left, the last one wholly above the window, by its bottom, so a reader past
   * everything marked moves with the end of what they passed. And the scroll it was at.
   */
  function place() {
    if (typeof scrollY !== "number") { return null; }
    var within = root();
    var across = [], below = null, above = [];
    var nodes = within.querySelectorAll("[" + ANCHOR_KEYS.join("], [") + "]");
    for (var i = 0; i < nodes.length && !below; i += 1) {
      var box = nodes[i].getBoundingClientRect();
      if (!shown(box)) { continue; }
      if (box.bottom <= 0) { above.push({ node: nodes[i], edge: "bottom", at: box.bottom }); } else if (box.top <= 0) {
        across.push({ node: nodes[i], edge: "top", at: box.top });
      } else { below = { node: nodes[i], edge: "top", at: box.top }; }
    }
    var marks = [];
    var take = function (candidate) {
      var mark = markOf(candidate.node);
      if (!mark || marked(within, mark).length !== 1) { return false; }
      mark.edge = candidate.edge;
      mark.at = candidate.at;
      marks.push(mark);
      return true;
    };
    across.reverse().concat(below ? [below] : []).forEach(take);
    for (var j = above.length - 1; j >= 0 && !take(above[j]); j -= 1) { /* the last one marked once */ }
    return { y: scrollY, marks: marks };
  }
  function moveTo(y) {
    if (typeof scrollTo === "function" && y !== scrollY) { scrollTo({ top: y, behavior: "instant" }); }
  }
  /**
   * Puts the reader back: the first of their marks still on the page once, and shown, where it stood; with none, the same
   * scroll as far as the page now holds.
   */
  function keep(at) {
    if (!at) { return; }
    var within = root();
    for (var i = 0; i < at.marks.length; i += 1) {
      var again = marked(within, at.marks[i]);
      var box = again.length === 1 ? again[0].getBoundingClientRect() : null;
      if (box && shown(box)) { moveTo(scrollY + box[at.marks[i].edge] - at.marks[i].at); return; }
    }
    if (scrollY === at.y) { return; }
    var most = document.documentElement.scrollHeight - innerHeight;
    moveTo(Math.max(0, Math.min(at.y, most)));
  }
  // Focus by key: the control focused before a redraw is found again by its data-key, in the same region: the nearest
  // holder a page names with data-region (the checking box, a stop's label), within the nearest holder with an id (the
  // detail panel, a chapter). A ticket in the panel, under its stop and in the checking box are told apart.
  function regionOf(node) {
    var named = node.parentElement ? node.parentElement.closest("[data-region]") : null;
    var holder = node.parentElement ? node.parentElement.closest("[id]") : null;
    if (named && holder && !holder.contains(named)) { named = null; }
    return (named ? named.getAttribute("data-region") + "@" : "") + (holder ? holder.id : "");
  }
  function focusMark() {
    var active = document.activeElement;
    if (!active || active === document.body || !active.getAttribute) { return null; }
    var mark = active.getAttribute("data-key");
    return mark ? { node: active, key: mark, region: regionOf(active) } : null;
  }
  /** Focuses the element carrying `key`, in `region` when one there carries it; true when one was found. */
  function focusKey(key, region) {
    // Matched by attribute value, never by a selector built from record text.
    var same = Array.prototype.filter.call(document.querySelectorAll("[data-key]"), function (node) { return node.getAttribute("data-key") === key; });
    var again = same.filter(function (node) { return regionOf(node) === region; })[0] || same[0];
    if (!again || typeof again.focus !== "function") { return false; }
    again.focus({ preventScroll: true });
    return true;
  }
  function refocus(mark) {
    if (!mark || mark.node.isConnected) { return; }
    focusKey(mark.key, mark.region);
  }
  function run(draw, why) {
    // The first draw opens the address the reader came by; there is no place to keep yet.
    var at = why === "first" ? null : place();
    var focused = focusMark();
    try { draw(); } finally {
      keep(at);
      refocus(focused);
      if (typeof requestAnimationFrame === "function") {
        drawnThisFrame = true;
        requestAnimationFrame(function () { drawnThisFrame = false; });
      }
    }
  }
  /**
   * Redraws the page with `draw`, keeping the reader's place. `why` names what asked for it: "first" for the draw that
   * opens the page, "read" or "push" for a new record, "status" for a new connection status, "reader" for the reader's own action, anything else ("tick",
   * "width", "runs", "plan") for a redraw of what is already said. Something new draws at once, as does the first redraw
   * in a frame; any more asked for in that frame merge into one, the last asked, drawn at the next frame unless something
   * new has been drawn by then.
   */
  function redraw(draw, why) {
    if (typeof draw !== "function") { throw new TypeError("page.redraw needs the page's draw function"); }
    if (typeof why !== "string" || !why) { throw new TypeError("page.redraw needs a reason"); }
    if (AT_ONCE[why] === true) {
      // The reader's own action draws only its part of the page, so a merged full redraw still waiting stays waiting.
      if (why !== "reader") { waiting = null; }
      run(draw, why);
      return;
    }
    if (!drawnThisFrame) { run(draw, why); return; }
    if (!waiting) {
      requestAnimationFrame(function () { var next = waiting; waiting = null; if (next) { run(next.draw, next.why); } });
    }
    waiting = { draw: draw, why: why };
  }

  // Where the reader is, and how a page moves: the one place the address and the page's scroll are written.
  /**
   * The kinds of navigation, as data. `address` says how one writes the address: "push" adds a history entry, "replace"
   * rewrites the current one, "push-if-chapter-changes" pushes only for another chapter, "none" leaves it. `scroll` says
   * how it moves the page, once: "top" or "end" of the chapter, "item" (the item centred, or the chapter's top with none),
   * "item-or-top" (the same, never smoothly), "keep" or "none". In the one-page layout a push writes as a replace, for
   * every reason: every chapter is on the one page, so Back leaves it. A feature adds a kind by declaring it here.
   */
  var reasons = {
    turn: { address: "push", scroll: "top" },
    "back-turn": { address: "push", scroll: "end" },
    link: { address: "push-if-chapter-changes", scroll: "item" },
    address: { address: "replace", scroll: "item-or-top" },
    history: { address: "none", scroll: "item-or-top" },
    layout: { address: "replace", scroll: "top" },
    first: { address: "replace", scroll: "item-or-top" },
    follow: { address: "replace", scroll: "none" },
  };
  var ADDRESS_WRITES = { push: true, replace: true, "push-if-chapter-changes": true, none: true };
  var SCROLLS = { top: true, end: true, item: true, "item-or-top": true, keep: true, none: true };
  // The page put under the controller (page.start), and where its reader is.
  var started = null;
  var here = { chapter: null, item: null, layout: "paged" };

  function chapterIds() { return started ? started.chapters() : []; }
  function layoutNow() { return started && typeof started.layout === "function" && started.layout() === "scroll" ? "scroll" : "paged"; }
  /** The chapter a node sits in: the nearest holder whose id names a chapter. */
  function chapterHolding(node) {
    var ids = chapterIds();
    for (var at = node; at && at.getAttribute; at = at.parentElement) {
      if (at.id && ids.indexOf(at.id) >= 0) { return at.id; }
    }
    return null;
  }
  /** What the address names: an element's id, read as written when it is a malformed escape. */
  function addressed() {
    var raw = (location.hash || "").slice(1);
    try { return decodeURIComponent(raw); } catch (error) { return raw; }
  }
  function addressOf(spot) {
    var ids = chapterIds();
    var name = spot.item || spot.chapter;
    // With no address the page opens on its first chapter; that is where it already is.
    return name === ids[0] && !spot.item ? ["", name] : [name];
  }
  function sameAddress(spot) { return addressOf(spot).indexOf(addressed()) >= 0; }
  function writeAddress(mode, spot) {
    if (mode === "none" || sameAddress(spot)) { return; }
    var url = "#" + (spot.item || spot.chapter);
    try {
      if (mode === "push") { history.pushState(null, "", url); } else { history.replaceState(null, "", url); }
    } catch (error) {
      // A page whose history can't be written (an opaque origin, a file in some browsers) moves its fragment instead;
      // the hashchange that follows names the address the controller is already at, so it routes nothing.
      try { if (mode === "push") { location.hash = url; } else { location.replace(url); } } catch (again) { /* the address stays */ }
    }
  }
  function scrollFor(rule, node, section, fromReader) {
    var layout = here.layout;
    var toTop = function () {
      var top = layout === "paged" || !section ? 0 : scrollY + section.getBoundingClientRect().top;
      scrollTo({ top: top, behavior: "instant" });
    };
    if (rule === "none" || rule === "keep") { return; }
    if (rule === "top") { toTop(); return; }
    if (rule === "end") {
      var end = layout === "paged" || !section ? document.documentElement.scrollHeight - innerHeight
        : scrollY + section.getBoundingClientRect().bottom - innerHeight;
      scrollTo({ top: Math.max(0, end), behavior: "instant" });
      return;
    }
    if (!node) { toTop(); return; }
    var box = node.getBoundingClientRect();
    var centred = scrollY + box.top - Math.max(0, (innerHeight - (box.bottom - box.top)) / 2);
    // An item a reader's own link reaches moves smoothly unless their motion check refuses; any other lands at once.
    scrollTo({ top: Math.max(0, centred), behavior: rule === "item" && motion(fromReader !== false) ? "smooth" : "instant" });
  }

  /**
   * The only way a page moves. `spot` is `{chapter, item}`: a chapter id, an element id within it, or both; `node` may
   * stand for an element with no id of its own, whose chapter the address then names. `reason` names an entry in
   * `page.reasons`, which says how the address is written and how the page scrolls, once.
   */
  function go(spot, reason) {
    var rule = Object.prototype.hasOwnProperty.call(reasons, reason) ? reasons[reason] : null;
    if (!rule || !ADDRESS_WRITES[rule.address] || !SCROLLS[rule.scroll]) { throw new TypeError("page.go: no such reason: " + reason); }
    if (!started) { return; }
    spot = spot || {};
    var ids = chapterIds();
    var node = spot.node || (spot.item ? document.getElementById(spot.item) : null);
    var chapter = (node ? chapterHolding(node) : null) || spot.chapter;
    if (ids.indexOf(chapter) < 0) { chapter = ids[0]; node = null; }
    var item = node && node.id && node.id !== chapter ? node.id : null;
    var to = { chapter: chapter, item: item };
    here.layout = layoutNow();
    var mode = rule.address;
    if (mode === "push-if-chapter-changes") { mode = chapter === here.chapter ? "replace" : "push"; }
    if (here.layout === "scroll" && mode === "push") { mode = "replace"; }
    writeAddress(mode, to);
    here.chapter = chapter;
    here.item = item;
    started.show(chapter);
    // Every fold holding the target opens, kept open by the store, before the page moves to it.
    if (node) { openHolding(node); }
    scrollFor(rule.scroll, node && node.id !== chapter ? node : null, document.getElementById(chapter), spot.fromReader);
  }
  /** Where the reader is: their chapter, the item the address names, and the layout. */
  function where() { return { chapter: here.chapter, item: here.item, layout: layoutNow() }; }

  /** Brings the page to what its address names; an address the controller wrote itself, or already followed, is not followed again. */
  function route(reason) {
    var name = addressed();
    if (here.chapter !== null && sameAddress({ chapter: here.chapter, item: here.item })) { return; }
    var ids = chapterIds();
    var named = name ? document.getElementById(name) : null;
    // An address naming something outside every chapter, as the hosted page's skip link to its main does, is the
    // browser's alone: the reader stays on their chapter.
    if (here.chapter !== null && ids.indexOf(name) < 0 && named && !chapterHolding(named)) { return; }
    go(ids.indexOf(name) >= 0 ? { chapter: name } : { item: name }, reason);
  }
  /** A same-page link to a chapter, or into one, goes through the controller; any other link is the browser's. */
  function followLink(event) {
    if (!started || event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
    var link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link) { return; }
    var href = link.getAttribute("href");
    if (!href || href.charAt(0) !== "#" || href.length < 2) { return; }
    var name = href.slice(1);
    try { name = decodeURIComponent(name); } catch (error) { /* as written */ }
    var target = document.getElementById(name);
    if (!target || !chapterHolding(target)) { return; }
    event.preventDefault();
    go(chapterIds().indexOf(name) >= 0 ? { chapter: name } : { item: name }, chapterIds().indexOf(name) >= 0 ? "turn" : "link");
  }
  /**
   * Puts a page under the controller: `chapters()` lists its chapter ids, in order; `show(id)` shows one, and never
   * scrolls; `layout()`, when given, says "paged" or "scroll". The page opens where its address says, and from then on
   * every same-page link, Back, Forward and outside address change goes through `go`.
   */
  function start(options) {
    if (started) { throw new Error("page.start: the page is already started"); }
    if (!options || typeof options.chapters !== "function" || typeof options.show !== "function") { throw new TypeError("page.start needs chapters() and show(id)"); }
    started = options;
    here.layout = layoutNow();
    document.addEventListener("click", followLink);
    addEventListener("popstate", function () { route("history"); });
    addEventListener("hashchange", function () { route("address"); });
    route("first");
  }

  // Regions that scroll sideways, each kept at its distance from its right edge across redraws.
  var sidewaysFromEnd = {};
  /** Registers a sideways-scrolling region, drawn again on every redraw, under `key`: its place is kept from its right edge. */
  function sideways(key, node) {
    if (typeof key !== "string" || !key || !node) { throw new TypeError("page.sideways needs a key and a node"); }
    if (node.scrollWidth > node.clientWidth) { node.scrollLeft = node.scrollWidth - node.clientWidth - (sidewaysFromEnd[key] || 0); }
    node.addEventListener("scroll", function () { sidewaysFromEnd[key] = Math.max(0, node.scrollWidth - node.clientWidth - node.scrollLeft); });
    return node;
  }

  // What is open, closed or picked: one store, by key, kept for the visit and, for a saved entry, in the browser's
  // storage under its own key, a text as it is and anything else as JSON, the formats the page has always written.
  var values = Object.create(null);
  var savedAs = Object.create(null);
  function readSaved(key) {
    var raw = null;
    try { raw = localStorage.getItem(key); } catch (error) { return undefined; }
    if (raw === null) { return undefined; }
    if (savedAs[key] !== "json") { return raw; }
    try { return JSON.parse(raw); } catch (error) { return undefined; }
  }
  /**
   * One entry in the store: `get()` and `set(value)`. `{saved: "text"}` (or `true`) keeps a text in the browser's
   * storage under `key` itself; `{saved: "json"}` keeps any value there as JSON. Unsaved entries last for the visit.
   */
  function state(key, options) {
    if (typeof key !== "string" || !key) { throw new TypeError("page.state needs a key"); }
    var saved = options && options.saved;
    if (saved) { savedAs[key] = saved === "json" ? "json" : "text"; }
    return {
      get: function () { return key in values ? values[key] : savedAs[key] ? readSaved(key) : undefined; },
      set: function (value) {
        values[key] = value;
        if (savedAs[key]) {
          try { localStorage.setItem(key, savedAs[key] === "json" ? JSON.stringify(value) : String(value)); } catch (error) { /* kept for the visit */ }
        }
        return value;
      },
    };
  }

  // The one fold: a details element, or a disclosure (a control with aria-expanded over a region it hides). Each is
  // known by the element it opens, so a link to anything inside one opens it.
  var folds = typeof WeakMap === "function" ? new WeakMap() : null;
  // The reader's own folds move by the page's motion rules, the run summary's height ease, where the page has it.
  function heldHeight(node) { return typeof runHeldHeight === "function" ? runHeldHeight(node) : 0; }
  function easeHeight(node, from) { if (typeof runEaseHeight === "function") { runEaseHeight(node, from); } }
  /**
   * Makes `node` a fold: a `details` element, or a disclosure `{button, region}`, whose button carries aria-expanded and
   * whose region, if it has one, is hidden while shut. Its open state is the store's entry `fold:<key>`: it is drawn
   * open from it, recorded there at the click, and opened by a link to anything inside it, or to `of`. `onOpen()` runs
   * whenever it opens, however; `onChange(open)` at the click; `onSettle()` when its height has changed; `can()`, when
   * given, says whether a click may open or shut it; `contain` keeps a disclosure's click from reaching what holds it.
   * `oneLine` says its summary is a single line when shut. Returns `node`.
   */
  function fold(node, options) {
    options = options || {};
    if (typeof options.key !== "string" || !options.key) { throw new TypeError("page.fold needs a key"); }
    var details = node && node.tagName === "DETAILS" ? node : null;
    var button = details ? details.querySelector("summary") : node && node.button;
    var region = details ? null : node && node.region || null;
    if (!button) { return node; }
    var record = state("fold:" + options.key);
    // Its control is keyed by the fold's own key, so focus comes back to it after a redraw.
    if (!button.hasAttribute("data-key")) { button.setAttribute("data-key", key("fold", options.key)); }
    var eased = details || (region ? region.parentNode : button);
    var settled = function () { if (typeof options.onSettle === "function") { options.onSettle(); } };
    function isOpen() { return details ? details.open : button.getAttribute("aria-expanded") === "true"; }
    function show(open) {
      if (details) { details.open = open; return; }
      button.setAttribute("aria-expanded", String(open));
      if (region) { region.hidden = !open; }
    }
    var entry = {
      // Opened or shut without the reader's own motion: by the store on a draw, by a link, or by the page.
      set: function (open) {
        record.set(open);
        if (isOpen() !== open) { show(open); }
        if (open && typeof options.onOpen === "function") { options.onOpen(); }
      },
    };
    [details, region, options.of].forEach(function (holder) { if (holder && folds) { folds.set(holder, entry); } });
    var want = record.get();
    if (want === true || want === false) { show(want); }
    if (isOpen() && typeof options.onOpen === "function") { options.onOpen(); }
    var closing = false;
    if (details) {
      // A fold the browser or the page opens itself (find in page, a toggle) is kept as it is left, as a click's is.
      details.addEventListener("toggle", function () {
        if (!details.isConnected || closing || record.get() === details.open) { return; }
        record.set(details.open);
        if (typeof options.onChange === "function") { options.onChange(details.open); }
        if (details.open && typeof options.onOpen === "function") { options.onOpen(); }
      });
    } else if (button.tagName !== "BUTTON") {
      button.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") { return; }
        event.preventDefault();
        button.click();
      });
    }
    button.addEventListener("click", function (event) {
      if (details) {
        // A link or button inside the summary keeps its own click.
        var inner = event.target && event.target.closest ? event.target.closest("a, button") : null;
        if (inner && button.contains(inner)) { return; }
        event.preventDefault();
      } else if (options.contain) {
        event.stopPropagation();
      }
      if (typeof options.can === "function" && !options.can()) { return; }
      var open = !isOpen();
      // Recorded now, not on the toggle event, which fires later: a redraw in between would rebuild it as it was.
      record.set(open);
      if (typeof options.onChange === "function") { options.onChange(open); }
      var from = heldHeight(eased);
      var moving = typeof eased.animate === "function" && motion(true);
      if (open) {
        show(true);
        if (typeof options.onOpen === "function") { options.onOpen(); }
        easeHeight(eased, from);
        var opening = typeof eased.getAnimations === "function" ? eased.getAnimations().filter(function (running) { return running.id === "run-height"; })[0] : null;
        if (opening && opening.finished) { opening.finished.then(settled, function () {}); }
      } else if (!details || !moving) {
        show(false);
        if (!details) { easeHeight(eased, from); }
      } else {
        // Shutting keeps the fold open until the slide ends, so nothing vanishes before it has moved.
        var summaryHeight = button.offsetHeight;
        var shut = options.oneLine ? parseFloat(getComputedStyle(button).lineHeight) || summaryHeight : summaryHeight;
        details.style.overflow = "hidden";
        closing = true;
        var sliding = details.animate([{ height: from + "px" }, { height: shut + "px" }], { id: "run-height", duration: Math.round(240 + Math.min(Math.abs(shut - from), 600) / 600 * 60), easing: "cubic-bezier(.77, 0, .175, 1)" });
        sliding.onfinish = function () { details.style.overflow = ""; show(false); closing = false; settled(); };
        sliding.oncancel = function () { details.style.overflow = ""; closing = false; };
      }
      settled();
    });
    return node;
  }
  /** Opens or shuts the fold `node` is, opens or stands for, without the reader's motion; false when it is no fold. */
  function open(node, wanted) {
    var entry = node && folds ? folds.get(node) : null;
    if (!entry) { return false; }
    entry.set(wanted !== false);
    return true;
  }
  /** Opens every fold holding `node`, and the one it stands for. */
  function openHolding(node) {
    for (var at = node; at && at.nodeType === 1; at = at.parentElement) {
      if (!open(at, true) && at.tagName === "DETAILS" && !at.open) { at.open = true; }
    }
  }

  return { motion: motion, key: key, redraw: redraw, reasons: reasons, go: go, here: where, start: start, sideways: sideways,
    state: state, fold: fold, open: open };
}());
