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
  var ANCHOR_KEYS = ["id", "data-subject", "data-ticket", "data-fold"];
  var drawnThisFrame = false;
  var waiting = null;
  // What a redraw is for, and whether it draws at once: something new to say (the page's first record, one read or one
  // pushed, or a new connection status) is shown at once and makes a merged redraw still waiting needless; a redraw of
  // what is already said (the clock, a new width, a refresh) merges with the others asked for in its frame.
  var AT_ONCE = { first: true, read: true, push: true, status: true };

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
   * The reader's place: the elements at the top of the window that only one element on the page is marked as, the
   * deepest across its top edge first, then the shallower ones holding it, then the first below the edge, each with how
   * far from that edge it sits; and the scroll it was at.
   */
  function place() {
    if (typeof scrollY !== "number") { return null; }
    var within = root();
    var across = [], below = null;
    var nodes = within.querySelectorAll("[" + ANCHOR_KEYS.join("], [") + "]");
    for (var i = 0; i < nodes.length && !below; i += 1) {
      var box = nodes[i].getBoundingClientRect();
      if (!shown(box)) { continue; }
      if (box.top <= 0 && box.bottom > 0) { across.push({ node: nodes[i], top: box.top }); } else if (box.top > 0) { below = { node: nodes[i], top: box.top }; }
    }
    var marks = [];
    across.reverse().concat(below ? [below] : []).forEach(function (candidate) {
      var mark = markOf(candidate.node);
      if (mark && marked(within, mark).length === 1) { mark.top = candidate.top; marks.push(mark); }
    });
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
      if (box && shown(box)) { moveTo(scrollY + box.top - at.marks[i].top); return; }
    }
    if (scrollY === at.y) { return; }
    var most = document.documentElement.scrollHeight - innerHeight;
    moveTo(Math.max(0, Math.min(at.y, most)));
  }
  function run(draw, why) {
    // The first draw opens the address the reader came by; there is no place to keep yet.
    var at = why === "first" ? null : place();
    try { draw(); } finally {
      keep(at);
      if (typeof requestAnimationFrame === "function") {
        drawnThisFrame = true;
        requestAnimationFrame(function () { drawnThisFrame = false; });
      }
    }
  }
  /**
   * Redraws the page with `draw`, keeping the reader's place. `why` names what asked for it: "first" for the draw that
   * opens the page, "read" or "push" for a new record, "status" for a new connection status, anything else ("tick",
   * "width", "runs", "plan") for a redraw of what is already said. Something new draws at once, as does the first redraw
   * in a frame; any more asked for in that frame merge into one, the last asked, drawn at the next frame unless something
   * new has been drawn by then.
   */
  function redraw(draw, why) {
    if (typeof draw !== "function") { throw new TypeError("page.redraw needs the page's draw function"); }
    if (typeof why !== "string" || !why) { throw new TypeError("page.redraw needs a reason"); }
    if (AT_ONCE[why] === true) { waiting = null; run(draw, why); return; }
    if (!drawnThisFrame) { run(draw, why); return; }
    if (!waiting) {
      requestAnimationFrame(function () { var next = waiting; waiting = null; if (next) { run(next.draw, next.why); } });
    }
    waiting = { draw: draw, why: why };
  }

  return { motion: motion, key: key, redraw: redraw };
}());
