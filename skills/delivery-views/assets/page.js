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

  return { motion: motion, key: key };
}());
