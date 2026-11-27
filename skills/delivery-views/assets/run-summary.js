// The round a reader picked in each run, kept across live redraws.
var runRoundPicked = {};
// The round each review last showed in flight, so the redraws that find it reduced settle its bead, for the length of
// that one motion (the page's --move).
var runInFlight = {};
var RUN_SETTLE_MS = 700;
// How a height change moves, by the repository's motion guidance (emil-design-eng): a morph on screen eases in and out
// on a strong curve, stays within 300ms (longer only as far as it travels), and never runs for a keyboard action, which a
// reader repeats too often to wait on.
var RUN_EASING = "cubic-bezier(.77, 0, .175, 1)";
function runEaseDuration(from, to) { return Math.round(240 + Math.min(Math.abs(to - from), 600) / 600 * 60); }
// A morph the reader's own action starts asks the page's one motion check as the reader's (page.js).
function runMotionWanted(node) {
  return typeof node.animate === "function" && page.motion(true);
}
/** The node's height now, mid-animation included, with any running height animation stopped so the next starts from it. */
function runHeldHeight(node) {
  var height = node.offsetHeight;
  if (typeof node.getAnimations === "function") { node.getAnimations().forEach(function (running) { if (running.id === "run-height") { running.cancel(); } }); }
  return height;
}
// Eases a node from the height it had before a change (taken with runHeldHeight) to the height it has now.
function runEaseHeight(node, from) {
  var to = node.offsetHeight;
  if (from === to || !runMotionWanted(node)) { return; }
  node.style.overflow = "hidden";
  var easing = node.animate([{ height: from + "px" }, { height: to + "px" }], { id: "run-height", duration: runEaseDuration(from, to), easing: RUN_EASING });
  easing.onfinish = easing.oncancel = function () { node.style.overflow = ""; };
}
/**
 * The page's one fold, for the run summary: page.fold (page.js), which slides open and closed by the page's motion rules,
 * is rebuilt open after a redraw when the reader left it open, and opens for a link to anything inside it. `options` are
 * page.fold's; `options.key` names the fold in the page's store.
 */
function runFold(fold, options) {
  return page.fold(fold, options || {});
}
/**
 * Draws icon parts on the 24px persona grid into an svg: { c: [cx, cy, r] } is a circle, { d } a path, and core: true
 * fills it, as an agent's core. The persona set, the lens icons and the marks drawn on that grid all go through here.
 */
function runDrawParts(svg, parts) {
  parts.forEach(function (part) {
    var shape = document.createElementNS("http://www.w3.org/2000/svg", part.c ? "circle" : "path");
    if (part.c) {
      shape.setAttribute("cx", String(part.c[0])); shape.setAttribute("cy", String(part.c[1])); shape.setAttribute("r", String(part.c[2]));
    } else {
      shape.setAttribute("d", part.d);
    }
    if (part.core) { shape.setAttribute("fill", "currentColor"); shape.setAttribute("stroke", "none"); }
    svg.appendChild(shape);
  });
}
/**
 * Words made readable without changing what they say: the one formatter every view's prose goes through, the run
 * summary's findings and fixes and the story's beats alike. It reads only what the words mark themselves, never a guess
 * from a word's shape:
 * - a `quoted` name reads as code;
 * - a parenthesised reference to a file's lines, "(path:12)" or "(path:12-18, other:3)", steps back to a faint aside,
 *   linked to the file at the reviewed commit when the page was given that link;
 * - a commit id, a change request's address and a ticket id the page knows open where they live.
 * `hooks` says what the host can link, each optional: `tickets` (the ids it knows) with `ticket(id)` making the node,
 * `commit(sha)` and `change(address)` each a node or null, and `file(ref)` an href or null. It returns the nodes in order.
 */
var RICH_REF = "[\\w./-]+:\\d+(?:-\\d+)?";
function richWords(words, hooks) {
  var said = words === undefined || words === null ? "" : String(words);
  hooks = hooks || {};
  var ids = (Array.isArray(hooks.tickets) ? hooks.tickets : []).filter(function (id) { return typeof id === "string" && id && said.indexOf(id) >= 0; })
    .sort(function (a, b) { return b.length - a.length; });
  var tickets = ids.length && typeof hooks.ticket === "function"
    ? "|(?:^|[^\\w-])(" + ids.map(function (id) { return id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|") + ")(?![\\w-])" : "";
  var pattern = new RegExp("`([^`\\n]+)`|\\((" + RICH_REF + "(?:,\\s*" + RICH_REF + ")*)\\)|(\\b[0-9a-f]{7,40}\\b)|(https?:\\/\\/[^\\s)]+)" + tickets, "g");
  var nodes = [], last = 0, found;
  function made(tag, className, value) {
    var element = document.createElement(tag);
    element.className = className;
    element.textContent = value;
    return element;
  }
  while ((found = pattern.exec(said))) {
    var match = found[1] !== undefined ? found[0] : found[2] !== undefined ? found[0] : found[3] || found[4] || found[5];
    var at = found.index + found[0].indexOf(match);
    if (at > last) { nodes.push(document.createTextNode(said.slice(last, at))); }
    if (found[1] !== undefined) {
      nodes.push(made("code", "prose-code", found[1]));
    } else if (found[2] !== undefined) {
      // Each reference in the parentheses on its own, so each can open its own file.
      var aside = made("span", "prose-refs", "");
      found[2].split(/,\s*/).forEach(function (ref, index) {
        if (index) { aside.appendChild(document.createTextNode(", ")); }
        var href = typeof hooks.file === "function" ? hooks.file(ref) : null;
        var node = made(typeof href === "string" && /^https:\/\//.test(href) ? "a" : "span", "prose-ref", ref);
        if (node.tagName === "A") {
          node.setAttribute("href", href);
          node.setAttribute("target", "_blank");
          node.setAttribute("rel", "noopener noreferrer");
          node.addEventListener("click", function (event) { event.stopPropagation(); });
        }
        aside.appendChild(node);
      });
      nodes.push(aside);
    } else if (found[3]) {
      var commit = typeof hooks.commit === "function" ? hooks.commit(found[3]) : null;
      nodes.push(commit || document.createTextNode(found[3]));
    } else if (found[4]) {
      // Punctuation that ends the sentence is not part of the address.
      var address = found[4].replace(/[.,;:]+$/, "");
      var change = typeof hooks.change === "function" ? hooks.change(address) : null;
      nodes.push(change || document.createTextNode(address));
      if (address.length < found[4].length) { nodes.push(document.createTextNode(found[4].slice(address.length))); }
    } else {
      nodes.push(hooks.ticket(found[5]));
    }
    last = at + match.length;
  }
  if (last < said.length) { nodes.push(document.createTextNode(said.slice(last))); }
  return nodes;
}
// A plan preview's words for every approving string below: a plan under agent review has had its lenses align, and no
// person has approved it. Under `options.preview` each string takes its wording here; without it, none changes. Every
// string literal in this file that matches /approv/i is a key here, but the enum values it compares and never shows.
var RUN_PREVIEW_WORDS = {
  "Approved on the first round": "Lenses aligned on the first round",
  "Approved on round ": "Lenses aligned on round ",
  " approved, notes open": " aligned, notes open",
  "approved": "aligned",
  "approved, notes open": "aligned, notes open",
  "Approved": "Aligned",
  "Approved with ": "Aligned with ",
  "Approved, notes open": "Aligned, notes open",
  "The plan's review has no round bound: it runs until every lens approves or the operator ends it.":
    "The plan's review has no round bound: it runs until every lens aligns or the operator ends it.",
};
// The values compared against and never displayed.
var RUN_PREVIEW_ENUMS = ["approve"];
function runSummaryNode(summary, options) {
  var preview = Boolean(options && options.preview === true);
  // A string as it reads here: a preview's wording for it, or the string itself.
  function say(words) { return preview && Object.prototype.hasOwnProperty.call(RUN_PREVIEW_WORDS, words) ? RUN_PREVIEW_WORDS[words] : words; }
  var SEVERITIES = ["P0", "P1", "P2", "P3"];
  var WORDS = { P0: "critical", P1: "serious", P2: "moderate", P3: "minor" };
  var PIPS_SHOWN = 8;
  function node(tag, className, value) {
    var made = document.createElement(tag);
    if (className) { made.className = className; }
    if (value !== undefined && value !== null) { made.textContent = String(value); }
    return made;
  }
  // A finding's or a fix's words through the shared formatter, its file references read at `sha`, the commit the
  // words were written about.
  function prose(className, words, sha) {
    var made = node("span", className);
    richWords(words, {
      file: function (ref) { return sha && options && typeof options.fileHref === "function" ? options.fileHref(sha, ref) : null; },
      tickets: options && Array.isArray(options.tickets) ? options.tickets : null,
      ticket: options && typeof options.ticket === "function" ? options.ticket : null,
    }).forEach(function (part) { made.appendChild(part); });
    return made;
  }
  function counted(findings) {
    var counts = { P0: 0, P1: 0, P2: 0, P3: 0 };
    if (findings && typeof findings === "object") {
      SEVERITIES.forEach(function (severity) { counts[severity] = Math.max(0, Number(findings[severity]) || 0); });
    }
    return counts;
  }
  function when(at) {
    if (typeof at !== "string") { return null; }
    var date = new Date(at);
    return isNaN(date.getTime()) ? null : date;
  }
  // "7:31 pm", "yesterday 7:31 pm" or "Oct 21, 7:31 pm".
  function clock(date) {
    var hours = date.getHours();
    var time = ((hours + 11) % 12 + 1) + ":" + (date.getMinutes() < 10 ? "0" : "") + date.getMinutes() + (hours < 12 ? " am" : " pm");
    var today = new Date(Date.now());
    var yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (date.toDateString() === today.toDateString()) { return time; }
    if (date.toDateString() === yesterday.toDateString()) { return "yesterday " + time; }
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][date.getMonth()] + " " + date.getDate() + ", " + time;
  }
  // How long after the round before it: "moments later", "4 min later", "2 h 5 min later", "3 days later";
  // null when it closed before the round before it.
  function later(from, to) {
    if (to.getTime() < from.getTime()) { return null; }
    var minutes = Math.round((to.getTime() - from.getTime()) / 60000);
    if (minutes < 1) { return "moments later"; }
    if (minutes < 60) { return minutes + " min later"; }
    if (minutes < 1440) { var rest = minutes % 60; return Math.floor(minutes / 60) + " h" + (rest ? " " + rest + " min" : "") + " later"; }
    var days = Math.round(minutes / 1440);
    return days + (days === 1 ? " day" : " days") + " later";
  }
  function timeNode(className, date, shown) {
    var made = node("time", className, shown);
    made.setAttribute("datetime", date.toISOString());
    return made;
  }
  // The shipped lens icons: the Line Map persona set's instruments, on its 24px grid in round 1.75 strokes. Each part is a
  // path (d) or a circle (c: cx, cy, r), built as SVG elements (never markup); a core part is filled rather than stroked.
  // The configuration names a lens's icon and name; a lens with no icon gets a round badge with its initial.
  var ICONS = {
    bullseye: [{ c: [12, 12, 8.5] }, { c: [12, 12, 4.6] }, { c: [12, 12, 1.4], core: true }],
    spy: [{ d: "M2.5 9.3C4.6 7.3 8.1 7 12 8.5C15.9 7 19.4 7.3 21.5 9.3C21.8 13.4 19.5 16.1 16.2 15.9C14.3 15.8 13.3 14.2 12 14.2C10.7 14.2 9.7 15.8 7.8 15.9C4.5 16.1 2.2 13.4 2.5 9.3Z" }, { d: "M5.6 10.9C6.9 10.4 8.4 10.7 9.7 11.8C8.3 12.6 6.5 12.4 5.6 10.9Z" }, { d: "M18.4 10.9C17.1 10.4 15.6 10.7 14.3 11.8C15.7 12.6 17.5 12.4 18.4 10.9Z" }],
    shield: [{ d: "M12 3C14.8 4.6 17.6 5.3 20 5.3C20 12.6 16.8 18.4 12 21C7.2 18.4 4 12.6 4 5.3C6.4 5.3 9.2 4.6 12 3Z" }, { d: "M12 8V16" }],
    bug: [{ d: "M8 10.5C8 8.3 9.8 6.5 12 6.5C14.2 6.5 16 8.3 16 10.5V14.5C16 16.7 14.2 18.5 12 18.5C9.8 18.5 8 16.7 8 14.5Z" }, { d: "M12 10.5V18.5" }, { d: "M10 6.9L8.6 4.6" }, { d: "M14 6.9L15.4 4.6" }, { d: "M8 11.5H4.5" }, { d: "M16 11.5H19.5" }, { d: "M8.3 15.5L5 17.5" }, { d: "M15.7 15.5L19 17.5" }],
    gauge: [{ d: "M5.6 18.4A9 9 0 1 1 18.4 18.4" }, { d: "M12 13L15.8 8.6" }, { c: [12, 13.5, 1.6], core: true }, { d: "M8.5 18.5H15.5" }],
    lock: [{ d: "M6 12.2C6 11.5 6.5 11 7.2 11H16.8C17.5 11 18 11.5 18 12.2V19.3C18 20 17.5 20.5 16.8 20.5H7.2C6.5 20.5 6 20 6 19.3Z" }, { d: "M8.5 11V7.8C8.5 5.9 10.1 4.3 12 4.3C13.9 4.3 15.5 5.9 15.5 7.8V11" }, { d: "M12 14.8V16.8" }],
    book: [{ d: "M12 6.8C10 5.3 7 4.8 3.5 5.3V18.3C7 17.8 10 18.3 12 19.8" }, { d: "M12 6.8C14 5.3 17 4.8 20.5 5.3V18.3C17 17.8 14 18.3 12 19.8" }, { d: "M12 6.8V19.8" }]
  };
  var LENSES = options && options.lenses && typeof options.lenses === "object" ? options.lenses : {};
  function configured(lens) {
    var entry = Object.prototype.hasOwnProperty.call(LENSES, lens) ? LENSES[lens] : null;
    return entry && typeof entry === "object" ? entry : null;
  }
  function lensIcon(lens) {
    var svgNs = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "run-lens-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    var entry = configured(lens);
    var parts = entry && typeof entry.icon === "string" && Object.prototype.hasOwnProperty.call(ICONS, entry.icon) ? ICONS[entry.icon] : null;
    if (!parts) {
      var ring = document.createElementNS(svgNs, "circle");
      ring.setAttribute("cx", "12"); ring.setAttribute("cy", "12"); ring.setAttribute("r", "9");
      svg.appendChild(ring);
      var letter = document.createElementNS(svgNs, "text");
      letter.setAttribute("x", "12"); letter.setAttribute("y", "16"); letter.setAttribute("text-anchor", "middle");
      letter.setAttribute("class", "run-lens-initial");
      letter.textContent = lensLabel(lens).charAt(0).toUpperCase();
      svg.appendChild(letter);
      return svg;
    }
    runDrawParts(svg, parts);
    return svg;
  }
  function lensName(lens) {
    var words = String(lens).replace(/^lens\./, "").split(/[-_.]/);
    return words[words.length - 1] || String(lens);
  }
  function noteWords(counts) {
    return SEVERITIES.filter(function (severity) { return counts[severity] > 0; }).map(function (severity) {
      return counts[severity] + " " + WORDS[severity] + (counts[severity] === 1 ? " note" : " notes");
    }).join(", ");
  }
  function story(rounds) {
    var last = rounds[rounds.length - 1];
    var total = { P0: 0, P1: 0, P2: 0, P3: 0 };
    var reported = false;
    rounds.forEach(function (round) {
      if (round.findings && typeof round.findings === "object") { reported = true; }
      var counts = counted(round.findings);
      SEVERITIES.forEach(function (severity) { total[severity] += counts[severity]; });
    });
    var head;
    var number = numberOf(last, rounds.length - 1);
    if (last.outcome === "approve") { head = number === 1 ? say("Approved on the first round") : say("Approved on round ") + number; }
    else if (last.outcome === "changes") { head = "Round " + number + " asked for changes"; }
    else if (last.outcome === "notes-open") { head = "Round " + number + say(" approved, notes open"); }
    else { head = rounds.length + (rounds.length === 1 ? " review round" : " review rounds"); }
    var raised = noteWords(total);
    var tail = !reported ? "findings not reported" : raised ? raised + " raised" : "nothing raised";
    return head + " · " + tail;
  }
  function outcomeOf(round) { return round.outcome === "approve" || round.outcome === "changes" || round.outcome === "notes-open" ? round.outcome : "unknown"; }
  // "approved", "asked for changes", "approved, notes open", "reported as <outcome>" for any other outcome, or "outcome not reported".
  function outcomeWords(round) {
    var outcome = outcomeOf(round);
    if (outcome === "approve") { return say("approved"); }
    if (outcome === "changes") { return "asked for changes"; }
    if (outcome === "notes-open") { return say("approved, notes open"); }
    return typeof round.outcome === "string" && round.outcome !== "" ? "reported as " + round.outcome : "outcome not reported";
  }
  function numberOf(round, index) { return round.round === null || round.round === undefined ? index + 1 : round.round; }
  // A plan review's fold covers several rounds as one entry: "2–10", and "Rounds 2–10".
  function isFold(round) { return typeof round.fromRound === "number" && typeof round.round === "number" && round.fromRound < round.round; }
  function dotOf(round, index) { return isFold(round) ? round.fromRound + "\u2013" + round.round : numberOf(round, index); }
  function nameOf(round, index) { return (isFold(round) ? "Rounds " : "Round ") + dotOf(round, index); }
  function plural(count, noun) { return count + " " + noun + (count === 1 ? "" : "s"); }
  // What an entry of a bounded plan file doesn't list: "This round doesn't list 5 findings, and some of its texts
  // were shortened."; a fold says how many rounds it holds.
  function clippedNode(round) {
    var clipped = round.clipped && typeof round.clipped === "object" ? round.clipped : {};
    var findings = Math.max(0, Number(clipped.findings) || 0), closures = Math.max(0, Number(clipped.closures) || 0);
    var unlisted = [findings ? plural(findings, "finding") : "", closures ? plural(closures, "closure") : ""].filter(Boolean).join(" or ");
    var words = isFold(round) ? "This entry folds " + (round.round - round.fromRound + 1) + " rounds" + (unlisted ? " and doesn't list " + unlisted : "")
      : unlisted ? "This round doesn't list " + unlisted : "";
    if (clipped.texts === true) { words = words ? words + ", and some of its texts were shortened" : "Some of this round's texts were shortened"; }
    return words ? node("p", "run-clipped", words + ".") : null;
  }
  function worst(counts) { return SEVERITIES.filter(function (severity) { return counts[severity] > 0; })[0] || null; }
  function bead(round, index, previous, picked, choose) {
    var outcome = outcomeOf(round);
    var number = numberOf(round, index);
    var counts = counted(round.findings);
    var total = counts.P0 + counts.P1 + counts.P2 + counts.P3;
    var item = node("li", "run-bead is-" + outcome);
    var button = node("button", "run-pick");
    button.type = "button";
    button.setAttribute("aria-pressed", String(picked));
    button.setAttribute("aria-label", nameOf(round, index) + ", " + outcomeWords(round) +
      (round.findings && typeof round.findings === "object" ? ", " + (noteWords(counts) || "nothing raised") : ""));
    button.appendChild(node("span", "run-dot", dotOf(round, index)));
    if (total) { button.appendChild(node("span", "run-badge is-" + worst(counts).toLowerCase(), total)); }
    button.addEventListener("click", function (event) { event.stopPropagation(); choose(index); });
    item.appendChild(button);
    var date = when(round.at);
    var before = previous ? when(previous.at) : null;
    if (date) { item.appendChild(timeNode("run-at", date, (before && later(before, date)) || clock(date))); }
    return item;
  }
  // A lens is named as the configuration names it; otherwise "lens.outcome-correctness" reads as "Correctness".
  function lensLabel(lens) {
    var entry = configured(lens);
    if (entry && typeof entry.name === "string" && entry.name) { return entry.name; }
    var name = lensName(lens);
    return name.charAt(0).toUpperCase() + name.slice(1);
  }
  // A lens's own verdict, never an inherited property for a lens named like one.
  function verdictOf(verdicts, lens) { return Object.prototype.hasOwnProperty.call(verdicts, lens) ? verdicts[lens] : undefined; }
  function verdictKind(verdict) { return verdict === "approve" || verdict === "changes" ? verdict : "unknown"; }
  function verdictWords(kind, verdict) { return kind === "approve" ? say("approved") : kind === "changes" ? "asked for changes" : String(verdict); }
  // A lens's icon and name; its verdict is said only where it differs from the round's, which the header gives.
  function reviewerHead(className, lens, verdict, outcome) {
    var kind = verdict === undefined ? "unknown" : verdictKind(verdict);
    var head = node("span", (className ? className + " " : "") + "run-lens is-" + kind);
    head.appendChild(lensIcon(lens));
    head.appendChild(node("b", "run-lens-name", lensLabel(lens)));
    if (verdict !== undefined && kind !== outcome) { head.appendChild(node("span", "run-lens-verdict", verdictWords(kind, verdict))); }
    return head;
  }
  // A finding's summary as a sentence: one stored without its full stop gets one, unless the renderer cut it at its
  // cap (SUMMARY_LENGTH, 280 characters), where a full stop would pass a cut sentence off as a whole one.
  var SUMMARY_CUT = 280;
  function sentenceOf(text) { return text + (text.length >= SUMMARY_CUT || /[.!?\u2026:)"'\u201D]$/.test(text) ? "" : "."); }
  function isNote(entry) { return entry && typeof entry === "object" && typeof entry.id === "string" && typeof entry.lens === "string" && typeof entry.summary === "string"; }
  function short(commit) { return typeof commit === "string" ? commit.slice(0, 7) : null; }
  // What later rounds said became of each finding, by its id: the first round to settle it wins.
  function fates(index) {
    // Keyed by finding id, with no inherited names, so an id like "constructor" finds nothing it did not set. Each id
    // keeps every lens's first word on it, since only the lens that filed a finding settles it.
    var known = Object.create(null);
    rounds.slice(index + 1).forEach(function (later, offset) {
      (Array.isArray(later.settled) ? later.settled : []).forEach(function (entry) {
        if (!entry || typeof entry.id !== "string") { return; }
        var said = known[entry.id] || (known[entry.id] = []);
        if (!said.some(function (other) { return other.entry.lens === entry.lens; })) { said.push({ entry: entry, round: numberOf(later, index + 1 + offset) }); }
      });
    });
    return known;
  }
  // A finding's fate from its own lens, or from a closure that names no lens, as a run's round report doesn't.
  function fateFor(known, entry) {
    var said = known[entry.id] || [];
    return said.filter(function (other) { return other.entry.lens === entry.lens; })[0]
      || said.filter(function (other) { return typeof other.entry.lens !== "string"; })[0] || null;
  }
  // A short commit, linked when the page can name its address and it is a full commit id.
  function commitNode(commit) {
    var href = /^[0-9a-f]{40}$/.test(commit) && options && typeof options.commitHref === "function" ? options.commitHref(commit) : null;
    if (typeof href !== "string" || !/^https:\/\//.test(href)) { return node("code", "run-commit", short(commit)); }
    var link = node("a", "run-commit", short(commit));
    link.setAttribute("href", href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    link.setAttribute("aria-label", "Open commit " + short(commit));
    link.addEventListener("click", function (event) { event.stopPropagation(); });
    return link;
  }
  // A line of words and commits: each part is a string or a list of commits, joined with commas.
  function line(className, parts) {
    var made = node("p", className);
    parts.forEach(function (part) {
      if (typeof part === "string") { made.appendChild(document.createTextNode(part)); return; }
      part.forEach(function (commit, index) {
        if (index) { made.appendChild(document.createTextNode(", ")); }
        made.appendChild(commitNode(commit));
      });
    });
    return made;
  }
  function commitsOf(entries) {
    var commits = [];
    entries.forEach(function (entry) {
      if (typeof entry.fixCommit === "string" && commits.indexOf(entry.fixCommit) < 0) { commits.push(entry.fixCommit); }
    });
    return commits;
  }
  // A finding's fate, as the run last observed it: fixed when a later round confirmed it, open, deferred, or any other
  // ending in words. `band` is how a band heading says it when every finding under it shares it.
  function fateOf(entry, fate) {
    if (entry.state === "resolved" && fate && fate.entry.state === "fixed") { return { kind: "fixed", words: "\u2713 fixed", band: "fixed" }; }
    if (entry.state === "unresolved") { return { kind: "open", words: "open", band: "open" }; }
    if (entry.state === "deferred") { return { kind: "deferred", words: "deferred", band: "deferred" }; }
    var other = entry.state === "resolved" && fate ? String(fate.entry.state) : String(entry.state);
    return { kind: "other", words: other, band: other };
  }
  // A deferral's follow-up, as one faint line under its finding: linked when it is a ticket the page can address, or
  // an address itself.
  function followUpNode(issue) {
    var made = node("p", "run-followup", "\u21b3 ");
    var href = /^https:\/\/\S+$/.test(issue) ? issue
      : options && typeof options.issueHref === "function" && /^[A-Z][A-Z0-9]*-[0-9]+$/.test(issue) ? options.issueHref(issue) : null;
    if (typeof href !== "string" || !/^https:\/\//.test(href)) { made.appendChild(document.createTextNode(issue)); return made; }
    var link = node("a", null, issue);
    link.setAttribute("href", href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    link.addEventListener("click", function (event) { event.stopPropagation(); });
    made.appendChild(link);
    return made;
  }
  // "round 4", "rounds 2 and 4", "rounds 2, 3 and 4".
  function roundsSaid(rounds) {
    var sorted = rounds.slice().sort(function (x, y) { return x - y; });
    if (sorted.length === 1) { return "round " + sorted[0]; }
    return "rounds " + sorted.slice(0, -1).join(", ") + " and " + sorted[sorted.length - 1];
  }
  // What became of the notes this round raised, as a sentence: "All 5 of this round's notes were fixed in 61f086d, and
  // round 2 confirmed them."
  function outcomeOfNotes(raised, known) {
    var fixed = raised.filter(function (entry) { return entry.state === "resolved" && fateFor(known, entry) && fateFor(known, entry).entry.state === "fixed"; });
    var open = raised.filter(function (entry) { return entry.state === "unresolved"; }).length;
    if (!fixed.length) {
      if (!open) { return null; }
      return node("p", "run-quiet", open === raised.length ? (open === 1 ? "This round's note is still open." : "All " + open + " of this round's notes are still open.")
        : open + " of this round's notes " + (open === 1 ? "is" : "are") + " still open.");
    }
    var confirmed = [];
    fixed.forEach(function (entry) { if (confirmed.indexOf(fateFor(known, entry).round) < 0) { confirmed.push(fateFor(known, entry).round); } });
    var commits = commitsOf(fixed.map(function (entry) { return fateFor(known, entry).entry; }));
    var head = fixed.length === raised.length ? (raised.length === 1 ? "This round's note was fixed" : "All " + raised.length + " of this round's notes were fixed")
      : fixed.length + " of this round's " + raised.length + " notes " + (fixed.length === 1 ? "was" : "were") + " fixed";
    return line("run-settled", [head + (commits.length ? " in " : ""), commits,
      ", and " + roundsSaid(confirmed) + " confirmed " + (fixed.length === 1 ? "it" : "them") + "." +
      (open ? " " + open + (open === 1 ? " is" : " are") + " still open." : "")]);
  }
  // How a confirmed note ended, in words: "fixed", "withdrawn" for a dropped one, or the ending as recorded.
  function endingOf(entry) { return entry.state === "fixed" ? "fixed" : entry.state === "dropped" ? "withdrawn" : String(entry.state); }
  // Earlier notes this round confirmed, as a sentence counting every ending, fixed first with its commits:
  // "This round confirmed 7 earlier notes: 6 fixed in 61f086d, 1 withdrawn."
  function settledNode(settled) {
    var notes = settled.filter(function (entry) { return entry && typeof entry === "object"; });
    var fixed = notes.filter(function (entry) { return entry.state === "fixed"; });
    var parts = ["This round confirmed " + notes.length + (notes.length === 1 ? " earlier note: " : " earlier notes: ")];
    var said = [];
    if (fixed.length) {
      var commits = commitsOf(fixed);
      said.push([fixed.length + " fixed" + (commits.length ? " in " : ""), commits]);
    }
    var endings = [];
    notes.forEach(function (entry) { if (entry.state !== "fixed" && endings.indexOf(endingOf(entry)) < 0) { endings.push(endingOf(entry)); } });
    endings.forEach(function (ending) {
      said.push([notes.filter(function (entry) { return entry.state !== "fixed" && endingOf(entry) === ending; }).length + " " + ending]);
    });
    said.forEach(function (pieces, index) { if (index) { parts.push(", "); } pieces.forEach(function (piece) { parts.push(piece); }); });
    parts.push(".");
    return line("run-settled", parts);
  }
  // What the round's report recorded of a lens's brief and result: each a toggle that shows the text in place,
  // a quiet "no brief recorded" when the report names none, or "unavailable" when it could not be read or did
  // not fit the page. A report that records no briefs shows neither. Named is for a row that names its lens.
  function briefsOf(round) {
    return round.briefs && typeof round.briefs === "object" && !Array.isArray(round.briefs) ? round.briefs : null;
  }
  function recordNode(round, lens, named) {
    var briefs = briefsOf(round);
    if (!briefs || !Object.prototype.hasOwnProperty.call(briefs, lens)) { return null; }
    var pair = briefs[lens] && typeof briefs[lens] === "object" ? briefs[lens] : {};
    var box = node("div", "run-record");
    var row = node("p", "run-record-row");
    row.hidden = true;
    if (named) { row.appendChild(node("b", "run-record-lens", lensLabel(lens))); }
    box.appendChild(row);
    [["brief", "Brief"], ["result", "Result"]].forEach(function (part) {
      var value = Object.prototype.hasOwnProperty.call(pair, part[0]) ? pair[part[0]] : null;
      if (value === null) { row.appendChild(node("span", "run-quiet", "no " + part[0] + " recorded")); return; }
      if (typeof value !== "object" || typeof value.text !== "string") { row.appendChild(node("span", "run-quiet", part[1] + " unavailable")); return; }
      var button = node("button", "run-record-pick", part[1]);
      button.type = "button";
      button.setAttribute("aria-expanded", "false");
      var shown = null;
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        if (shown) { shown.remove(); shown = null; button.setAttribute("aria-expanded", "false"); return; }
        shown = node("div", "run-record-text is-" + part[0]);
        shown.appendChild(node("pre", null, value.text));
        if (value.shortened === true) { shown.appendChild(node("p", "run-quiet", "Shortened: the first 64 KiB are shown.")); }
        box.appendChild(shown);
        button.setAttribute("aria-expanded", "true");
      });
      row.appendChild(button);
    });
    return box;
  }
  // A deferral: a finding its lens filed as deferred, which the card says why it waited and how it ended. A P0 is never
  // deferred, so one filed so is drawn as any other finding.
  var REASONS = { P1: "Outside this round's change, so it doesn't block this story", P2: "Moderate, so it doesn't block",
                  P3: "Minor, so it doesn't block" };
  function severityOf(entry) { return SEVERITIES.indexOf(entry.severity) < 0 ? "P3" : entry.severity; }
  function isDeferral(entry) { return entry.filed === "deferred" && Object.prototype.hasOwnProperty.call(REASONS, severityOf(entry)); }
  // Why it waited, in full, from the same rule as its one-line reason.
  function whyItWaited(entry) {
    var lens = lensLabel(entry.lens), severity = severityOf(entry);
    if (severity === "P1") { return "Serious (P1), filed new by a verification round against lines outside the round's change, so " + lens + " deferred it to a follow-up."; }
    if (severity === "P2") { return "Moderate (P2). A finding at this level doesn't block landing, so " + lens + " deferred it to a follow-up."; }
    return "Minor (P3). Minor findings never block landing, so " + lens + " deferred it to a follow-up.";
  }
  // The follow-up the close-out filed for a deferral, when the record names its key.
  function filedAs(entry) {
    var key = options && typeof options.followUpOf === "function" ? options.followUpOf(entry.id) : null;
    return typeof key === "string" && key ? key : null;
  }
  // What became of a deferral, read from its own lens's later reports only, as review-work's convergence rule says; a
  // closure that names no lens, as a run's round report gives it, is the finding's own.
  // The first later round of that lens to fix, close or withdraw it ends it; one that lists it deferred or open, or
  // leaves it out, left it waiting. A round without that lens, or one whose details could not be read and that holds no
  // word of the lens on it, says nothing of it. `history` is each later round's word on it, for the detail.
  function deferralFate(entry, index) {
    var history = [], ended = null, last = null;
    rounds.slice(index + 1).forEach(function (later, offset) {
      if (ended) { return; }
      var number = numberOf(later, index + 1 + offset);
      last = number;
      var own = (Array.isArray(later.settled) ? later.settled : []).filter(function (other) {
        // A run's round report names no lens on its closures; an id is its raising lens's alone, so one counts there.
        return other && other.id === entry.id && (other.lens === entry.lens || typeof other.lens !== "string");
      })[0];
      var verdicts = later.verdicts && typeof later.verdicts === "object" ? later.verdicts : {};
      if (!own) {
        // A round only leaves it out when the filing lens reported there and the round's details could be read; a
        // round whose details are unavailable, as when another lens is missing, still counts its own closures above.
        if (later.detailsUnavailable !== true && Object.prototype.hasOwnProperty.call(verdicts, entry.lens) && verdicts[entry.lens] !== "unavailable") { history.push({ round: number, words: "Left out of " + lensLabel(entry.lens) + "'s report, so it is still deferred." }); }
        return;
      }
      var resolution = typeof own.resolution === "string" && own.resolution ? own.resolution : null;
      // The renderer's words, or a round report's own: "closed" and "withdrawn" are kept as given there.
      if (own.state === "fixed" || own.state === "closed") {
        var closed = own.closedAs === "closed" || own.state === "closed";
        ended = { kind: closed ? "closed" : "fixed", round: number };
        history.push({ round: number, words: (closed ? "Closed" : "Fixed") + (resolution ? ": " + resolution : ".") });
      } else if (own.state === "dropped" || own.state === "withdrawn") {
        ended = { kind: "withdrawn", round: number };
        history.push({ round: number, words: "Withdrawn" + (resolution ? ": " + resolution : ".") });
      } else {
        history.push({ round: number, words: "Still deferred" + (resolution ? ": " + resolution : ".") });
      }
    });
    var fate = ended || (last === null ? { kind: "last", round: null } : { kind: "waiting", round: last });
    var key = ended ? null : filedAs(entry);
    if (key) { fate = { kind: "filed", round: fate.round, key: key }; }
    fate.history = history;
    return fate;
  }
  function fateNode(fate) {
    var words = fate.kind === "fixed" ? "✓ fixed in round " + fate.round : fate.kind === "closed" ? "✓ closed in round " + fate.round
      : fate.kind === "withdrawn" ? "withdrawn in round " + fate.round : fate.kind === "waiting" ? "still waiting after round " + fate.round
      : fate.kind === "last" ? "no later round to check it · becomes a follow-up at close-out" : null;
    var made = node("span", "run-fate is-" + fate.kind, words);
    if (fate.kind === "filed") {
      made.appendChild(document.createTextNode("Filed as "));
      made.appendChild(issueNode(fate.key));
    }
    return made;
  }
  // A follow-up as text: linked when it is a ticket the page can address, or an address itself.
  function issueNode(key) {
    var href = /^https:\/\/\S+$/.test(key) ? key
      : options && typeof options.issueHref === "function" && /^[A-Z][A-Z0-9]*-[0-9]+$/.test(key) ? options.issueHref(key) : null;
    if (typeof href !== "string" || !/^https:\/\//.test(href)) { return document.createTextNode(key); }
    var link = node("a", null, key);
    link.setAttribute("href", href);
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    link.addEventListener("click", function (event) { event.stopPropagation(); });
    return link;
  }
  // A lens's deferrals and their fates, counted: "deferred 10: 7 fixed by round 2, 3 still waiting".
  function deferralCount(fates) {
    var parts = [];
    [["fixed", "fixed by round "], ["closed", "closed by round "]].forEach(function (pair) {
      var own = fates.filter(function (fate) { return fate.kind === pair[0]; });
      if (own.length) { parts.push(own.length + " " + pair[1] + Math.max.apply(null, own.map(function (fate) { return fate.round; }))); }
    });
    [["withdrawn", "withdrawn"], ["filed", "filed"], ["waiting", "still waiting"], ["last", "with no later round"]].forEach(function (pair) {
      var count = fates.filter(function (fate) { return fate.kind === pair[0]; }).length;
      if (count) { parts.push(count + " " + pair[1]); }
    });
    return "deferred " + fates.length + ": " + parts.join(", ");
  }
  // One deferral's row: its text, a real button that opens its detail, then its reason and fate on one quiet line. The
  // detail says why it waited, what could break, the change it proposes, its follow-up and how it ended, round by
  // round. Closed, the row is as tall as it is with nothing open.
  function deferralRow(round, index, entry) {
    var severity = severityOf(entry);
    var fate = deferralFate(entry, index);
    var item = node("li", "run-finding is-" + severity.toLowerCase() + " is-deferral");
    var key = "deferral:" + (summary.observationKey || summary.runId || "") + ":" + numberOf(round, index) + ":" + entry.lens + ":" + entry.id;
    var toggle = node("button", "run-deferral");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    var said = node("span", "run-finding-line");
    said.appendChild(prose("run-finding-text", sentenceOf(entry.summary), round.candidateRef));
    toggle.appendChild(said);
    item.appendChild(toggle);
    var meta = node("p", "run-deferral-said");
    meta.appendChild(node("span", "run-reason", REASONS[severity]));
    meta.appendChild(document.createTextNode(" · "));
    meta.appendChild(fateNode(fate));
    item.appendChild(meta);
    var detail = node("dl", "run-deferral-detail");
    detail.hidden = true;
    var recorded = entry.detail && typeof entry.detail === "object" ? entry.detail : {};
    var field = function (label, value) {
      detail.appendChild(node("dt", null, label));
      var said = node("dd", null);
      if (value === null || value === undefined) { said.className = "run-quiet"; said.textContent = "not recorded"; }
      else if (typeof value === "string") { said.textContent = value; }
      else { said.appendChild(value); }
      detail.appendChild(said);
    };
    var text = function (value) { return typeof value === "string" && value ? value : null; };
    field("Why it waited", whyItWaited(entry));
    field("What could break", text(recorded.failureScenario));
    field("Proposed change", text(recorded.proposedChange));
    var follow = text(recorded.followUp);
    field("Its follow-up", follow || (text(entry.deferredIssue) ? issueNode(entry.deferredIssue) : null));
    var ended = node("ol", "run-deferral-history");
    var step = function (number, words) {
      var made = node("li", null);
      made.appendChild(node("span", "run-deferral-round", number === null ? "—" : "R" + number));
      made.appendChild(typeof words === "string" ? node("span", null, words) : words);
      ended.appendChild(made);
    };
    step(numberOf(round, index), "Raised and deferred by " + lensLabel(entry.lens) + ".");
    fate.history.forEach(function (said) { step(said.round, said.words); });
    if (fate.kind === "last") { step(null, "No later round to check it; it becomes a follow-up at close-out."); }
    if (fate.kind === "filed") {
      var filed = node("span", null, "Filed as ");
      filed.appendChild(issueNode(fate.key));
      filed.appendChild(document.createTextNode(" at close-out."));
      step(null, filed);
    }
    field("How it ended", ended);
    item.appendChild(detail);
    // A disclosure on the page's one fold: its button and the detail it shows, the row easing to its new height.
    page.fold({ button: toggle, region: detail }, { key: key });
    return item;
  }
  // The finding an earlier round raised, by its id: the latest round before this one that raised it, or null when
  // no round on the page holds it.
  function raisedBefore(round, id) {
    var found = null;
    rounds.slice(0, Math.max(0, rounds.indexOf(round))).forEach(function (earlier) {
      (Array.isArray(earlier.raised) ? earlier.raised : []).forEach(function (entry) { if (isNote(entry) && entry.id === id) { found = { entry: entry, round: earlier }; } });
    });
    return found;
  }
  // One confirmed note, a fold of its own. It leads with what its lens found and how serious it was, from the round
  // that raised it; opened, it shows how it was settled, and the commit or follow-up that shows it. A
  // note whose finding no round on the page holds leads with its resolution, as before. A tick stands for "fixed",
  // said in words only for a screen reader, and any other ending is named.
  function closureRow(round, entry) {
    var item = node("li", "");
    var raised = raisedBefore(round, entry.id);
    var finding = raised ? raised.entry : null;
    var disclosure = node("details", "run-closures is-" + (entry.state === "fixed" ? "fixed" : "other"));
    var preview = node("summary", "run-finding");
    if (finding) {
      var severity = SEVERITIES.indexOf(finding.severity) < 0 ? "P3" : finding.severity;
      preview.appendChild(prose("run-finding-text", sentenceOf(finding.summary), raised.round.candidateRef));
      preview.appendChild(node("span", "run-closure-sev is-" + severity.toLowerCase(), WORDS[severity]));
    } else {
      preview.appendChild(node("span", "run-finding-text", entry.resolution));
    }
    preview.appendChild(node("span", "run-fate", endingOf(entry)));
    disclosure.appendChild(preview);
    if (finding) {
      // What was done about it, set apart from the finding above: a sheet that leads with how it ended and the commit
      // that shows it, then the resolution in its own words, then any follow-up.
      var ending = endingOf(entry);
      var fix = node("div", "run-closure-fix");
      var head = typeof entry.fixCommit === "string"
        ? line("run-closure-evidence", [ending.charAt(0).toUpperCase() + ending.slice(1) + " in ", [entry.fixCommit]])
        : node("p", "run-closure-evidence", ending.charAt(0).toUpperCase() + ending.slice(1));
      // A short opening verdict ("Adopted as written.") joins the head, so the body starts with what changed.
      var resolution = String(entry.resolution);
      var verdict = /^([^.;:()]{3,40})\.\s+(?=\S)/.exec(resolution);
      if (verdict && verdict[1].split(/\s+/).length <= 5) {
        head.appendChild(node("span", "run-closure-verdict", verdict[1]));
        resolution = resolution.slice(verdict[0].length);
      }
      fix.appendChild(head);
      var body = node("p", "run-closure-resolution");
      body.appendChild(prose("", resolution, entry.fixCommit));
      fix.appendChild(body);
      if (typeof entry.deferredIssue === "string" && entry.deferredIssue) { fix.appendChild(followUpNode(entry.deferredIssue)); }
      disclosure.appendChild(fix);
    } else {
      // With no finding to lead with, the note's id is all the fold has to add.
      disclosure.appendChild(node("p", "run-closure", entry.id));
    }
    item.appendChild(runFold(disclosure, { key: "closure:" + (summary.observationKey || summary.runId || "") + ":" + round.round + ":" + entry.id, oneLine: true }));
    return item;
  }
  // The earlier notes a round confirmed: its sentence, then each lens's notes. A lens whose notes were all fixed is
  // one line, "Testing · 6 fixed", that opens on them; in a lens with any other ending, the notes that ended otherwise
  // stay in sight, each named, and its fixed ones fold beneath a count of their own.
  function settledParts(round) {
    var outcome = outcomeOf(round);
    var verdicts = round.verdicts && typeof round.verdicts === "object" ? round.verdicts : {};
    var settled = Array.isArray(round.settled) ? round.settled : [];
    var parts = settled.length ? [settledNode(settled)] : [];
    var closures = settled.filter(function (entry) { return summary.observational && entry && typeof entry.resolution === "string"; });
    var closureLenses = [];
    closures.forEach(function (entry) { if (closureLenses.indexOf(entry.lens) < 0) { closureLenses.push(entry.lens); } });
    var foldKey = function (name) { return "closures:" + (summary.observationKey || summary.runId || "") + ":" + round.round + ":" + name; };
    closureLenses.sort().forEach(function (lens) {
      var own = closures.filter(function (entry) { return entry.lens === lens; });
      var fixed = own.filter(function (entry) { return entry.state === "fixed"; });
      var others = own.filter(function (entry) { return entry.state !== "fixed"; });
      var group = node("div", "run-reviewer run-closure-reviewer");
      var head = reviewerHead("run-reviewer-head", lens, verdictOf(verdicts, lens), outcome);
      var fixedList = node("ul", "run-findings");
      fixed.forEach(function (entry) { fixedList.appendChild(closureRow(round, entry)); });
      if (!others.length) {
        var whole = node("details", "run-closure-lens");
        var top = node("summary", "run-closure-count");
        head.appendChild(node("span", "run-closure-fixed", " \u00b7 " + fixed.length + " fixed"));
        top.appendChild(head);
        whole.appendChild(top);
        whole.appendChild(fixedList);
        group.appendChild(runFold(whole, { key: foldKey(lens) }));
        parts.push(group);
        return;
      }
      group.appendChild(head);
      var list = node("ul", "run-findings");
      others.forEach(function (entry) { list.appendChild(closureRow(round, entry)); });
      group.appendChild(list);
      if (fixed.length) {
        var folded = node("details", "run-closure-lens");
        folded.appendChild(node("summary", "run-closure-count", fixed.length + " fixed"));
        folded.appendChild(fixedList);
        group.appendChild(runFold(folded, { key: foldKey(lens + ":fixed") }));
      }
      parts.push(group);
    });
    return parts;
  }
  // How long ago something began, for a round or a lens still at work: "moments", "4 min", "2 h 5 min", "3 days".
  function since(date) {
    var minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
    if (minutes < 1) { return "moments"; }
    if (minutes < 60) { return minutes + " min"; }
    if (minutes < 1440) { var rest = minutes % 60; return Math.floor(minutes / 60) + " h" + (rest ? " " + rest + " min" : ""); }
    var days = Math.round(minutes / 1440);
    return days + (days === 1 ? " day" : " days");
  }
  // The round in flight's lenses: those it started with, then any other that came back.
  function runningLenses() {
    var lenses = running.lenses.slice();
    Object.keys(running.back).forEach(function (lens) { if (lenses.indexOf(lens) < 0) { lenses.push(lens); } });
    return lenses;
  }
  function backOf(lens) { return Object.prototype.hasOwnProperty.call(running.back, lens) ? running.back[lens] : null; }
  function backCount() { return runningLenses().filter(backOf).length; }
  // The round in flight: the head it reads, how long ago it began, how many lenses are back, the lane's reason when
  // it gave one, a row per lens, back with its verdict or still reading, and the last round's confirmed notes folded.
  function runningCard(previous) {
    var lenses = runningLenses();
    var box = node("div", "run-card is-running");
    var head = node("p", "run-card-head");
    head.appendChild(node("b", null, "Round " + running.round));
    head.appendChild(node("span", "run-card-outcome", backCount() + " of " + lenses.length + " back"));
    box.appendChild(head);
    var began = when(running.at);
    var reading = line("run-quiet run-reading-at", ["Reviewing ", [running.head]]);
    if (began) {
      reading.appendChild(document.createTextNode(" · started "));
      reading.appendChild(timeNode("run-since", began, since(began) + " ago"));
    }
    box.appendChild(reading);
    if (typeof running.why === "string" && running.why.trim()) { box.appendChild(node("p", "run-why", running.why)); }
    var rows = node("ul", "run-lens-rows");
    lenses.forEach(function (lens) {
      var back = backOf(lens);
      var row = node("li", "run-lens-row " + (back ? "is-back" : "is-reading"));
      row.setAttribute("data-lens", lens);
      if (back) {
        row.appendChild(reviewerHead("", lens, back.verdict, null));
        var counts = counted(back.findings);
        row.appendChild(node("span", "run-quiet", noteWords(counts) || "nothing raised"));
      } else {
        row.appendChild(reviewerHead("", lens, undefined, null));
        row.appendChild(node("span", "run-reading", "reading" + (began ? " · " + since(began) : "")));
      }
      rows.appendChild(row);
    });
    box.appendChild(rows);
    var earlier = previous ? settledParts(previous) : [];
    if (earlier.length) {
      // The notes the last round confirmed fold to their one sentence while a round runs, and open as they do today.
      var fold = node("details", "run-earlier");
      var said = node("summary", "run-earlier-head");
      while (earlier[0].firstChild) { said.appendChild(earlier[0].firstChild); }
      said.firstChild.textContent = said.firstChild.textContent.replace(/^This round/, nameOf(previous, rounds.length - 1));
      fold.appendChild(said);
      earlier.slice(1).forEach(function (part) { fold.appendChild(part); });
      box.appendChild(runFold(fold, { key: "earlier:" + key + ":" + running.round, oneLine: true }));
    }
    return box;
  }
  // The round in flight's bead: a ring in the lane's colour whose halo breathes on the page's tempo.
  function runningBead(picked, choose) {
    var item = node("li", "run-bead is-running");
    var button = node("button", "run-pick");
    button.type = "button";
    button.setAttribute("aria-pressed", String(picked));
    button.setAttribute("aria-label", "Round " + running.round + ", under way, " + backCount() + " of " + runningLenses().length + " lenses back");
    button.appendChild(node("span", "run-dot", running.round));
    button.addEventListener("click", function (event) { event.stopPropagation(); choose(); });
    item.appendChild(button);
    var began = when(running.at);
    if (began) { item.appendChild(timeNode("run-at", began, "under way")); }
    return item;
  }
  // The picked round in full. With the round's report, each reviewer lists what it raised and what became
  // of it; without one, the lenses' verdicts and the round's counts.
  function card(round, index) {
    var outcome = outcomeOf(round);
    var box = node("div", "run-card is-" + outcome);
    var head = node("p", "run-card-head");
    if (summary.observational && typeof round.candidateRef === "string") {
      // Which commit the round read, as a sentence; the commit opens on the forge when the renderer gave its address.
      box.appendChild(line("run-quiet", ["Reviewed at ", [round.candidateRef]]));
    }
    head.appendChild(node("b", null, nameOf(round, index)));
    // An approval that came with findings its lenses filed as deferred counts them, whatever later rounds made of them.
    var deferrable = Array.isArray(round.raised) && !isFold(round)
      ? round.raised.filter(function (entry) { return isNote(entry) && entry.filed === "deferred"; }).length : 0;
    var pill = outcome === "approve" ? (deferrable ? say("Approved with ") + deferrable + (deferrable === 1 ? " deferrable note" : " deferrable notes") : say("Approved"))
      : outcome === "changes" ? "Changes requested" : outcome === "notes-open" ? say("Approved, notes open") : outcomeWords(round);
    head.appendChild(node("span", "run-card-outcome", pill.charAt(0).toUpperCase() + pill.slice(1)));
    box.appendChild(head);
    var verdicts = round.verdicts && typeof round.verdicts === "object" ? round.verdicts : {};
    if (Array.isArray(round.raised) && !isFold(round)) {
      var raised = round.raised.filter(isNote);
      var lenses = Object.keys(verdicts);
      raised.forEach(function (entry) { if (lenses.indexOf(entry.lens) < 0) { lenses.push(entry.lens); } });
      Object.keys(briefsOf(round) || {}).forEach(function (lens) { if (lenses.indexOf(lens) < 0) { lenses.push(lens); } });
      var known = fates(index);
      // When every finding is fixed, or every one is still open, the footer says so once and the rows carry no fate.
      var fixedAll = raised.length && raised.every(function (entry) { return fateOf(entry, fateFor(known, entry)).kind === "fixed"; });
      var openAll = raised.length && raised.every(function (entry) { return entry.state === "unresolved"; });
      var uniform = Boolean(fixedAll || openAll);
      // A round that raised nothing is the reviewers in one row and one line, not a "nothing raised" each.
      if (!raised.length) {
        var quiet = node("p", "run-card-lenses");
        lenses.sort().forEach(function (lens) { quiet.appendChild(reviewerHead("", lens, verdictOf(verdicts, lens), outcome)); });
        if (lenses.length) { box.appendChild(quiet); }
        box.appendChild(node("p", "run-quiet", "Nothing new raised"));
        lenses.forEach(function (lens) { var record = recordNode(round, lens, true); if (record) { box.appendChild(record); } });
      }
      else lenses.sort().forEach(function (lens) {
        var own = raised.filter(function (entry) { return entry.lens === lens; }).sort(function (a, b) {
          return SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity);
        });
        var group = node("div", "run-reviewer");
        var top = reviewerHead("run-reviewer-head", lens, verdictOf(verdicts, lens), outcome);
        if (!own.length) { top.appendChild(node("span", "run-quiet", "nothing raised")); }
        var deferrals = own.filter(isDeferral);
        if (deferrals.length) { top.appendChild(node("span", "run-deferral-count", deferralCount(deferrals.map(function (entry) { return deferralFate(entry, index); })))); }
        group.appendChild(top);
        var record = recordNode(round, lens, false);
        if (record) { group.appendChild(record); }
        if (own.length) {
          // Each severity is said once, as a quiet band heading over its findings, worst first, with the fate they
          // share said once beside it; a finding says its own fate only when it differs. A minor finding's text is
          // lighter than a worse one's.
          var list = node("ul", "run-findings");
          var band = null, shared = null;
          var severityOf = function (entry) { return SEVERITIES.indexOf(entry.severity) < 0 ? "P3" : entry.severity; };
          own.forEach(function (entry) {
            var severity = severityOf(entry);
            var fate = fateOf(entry, fateFor(known, entry));
            if (severity !== band) {
              band = severity;
              var members = own.filter(function (other) { return severityOf(other) === severity; });
              var bands = members.map(function (other) { return fateOf(other, fateFor(known, other)).band; });
              // A band holding a deferral says no shared fate: each deferral says its own beside its reason.
              shared = uniform || members.some(isDeferral) ? null : bands.every(function (word) { return word === bands[0]; }) ? bands[0] : null;
              var name = WORDS[severity];
              list.appendChild(node("li", "run-band is-" + severity.toLowerCase(), name.charAt(0).toUpperCase() + name.slice(1)
                + (members.length > 1 ? " · " + members.length : "") + (shared ? " · " + (members.length > 1 ? "all " : "") + shared : "")));
            }
            if (isDeferral(entry)) { list.appendChild(deferralRow(round, index, entry)); return; }
            var item = node("li", "run-finding is-" + severity.toLowerCase());
            // Summaries are sentences; one stored without its full stop gets one here.
            var said = node("span", "run-finding-line");
            said.appendChild(prose("run-finding-text", sentenceOf(entry.summary), round.candidateRef));
            if (!uniform && fate.band !== shared) {
              said.appendChild(document.createTextNode(" "));
              said.appendChild(node("span", "run-fate is-" + fate.kind, fate.words));
            }
            item.appendChild(said);
            var issue = entry.deferredIssue || (fateFor(known, entry) && fateFor(known, entry).entry.deferredIssue);
            if ((entry.filed === "deferred" || fate.kind === "deferred") && fate.kind !== "fixed" && typeof issue === "string" && issue) { item.appendChild(followUpNode(issue)); }
            list.appendChild(item);
          });
          group.appendChild(list);
        }
        box.appendChild(group);
      });
      var told = outcomeOfNotes(raised, known);
      if (told) { box.appendChild(told); }
      settledParts(round).forEach(function (part) { box.appendChild(part); });
      var listed = clippedNode(round);
      if (listed) { box.appendChild(listed); }
      return box;
    }
    var names = Object.keys(verdicts).sort();
    if (names.length) {
      var row = node("p", "run-card-lenses");
      names.forEach(function (lens) { row.appendChild(reviewerHead("", lens, verdictOf(verdicts, lens), outcome)); });
      box.appendChild(row);
    }
    var notes = node("p", "run-card-notes");
    var counts = counted(round.findings);
    var severities = SEVERITIES.filter(function (severity) { return counts[severity] > 0; });
    if (!(round.findings && typeof round.findings === "object")) { notes.appendChild(node("span", "run-clean", "Findings not reported")); }
    else if (!severities.length) { notes.appendChild(node("span", "run-clean", "Nothing raised")); }
    else {
      var total = counts.P0 + counts.P1 + counts.P2 + counts.P3;
      notes.appendChild(node("b", "run-card-total", total + (total === 1 ? " note" : " notes")));
      severities.forEach(function (severity) {
        notes.appendChild(node("span", "run-sev is-" + severity.toLowerCase(), counts[severity] + " " + WORDS[severity]));
      });
    }
    if (round.detailsUnavailable === true) { notes.appendChild(node("span", "run-clean", "details unavailable")); }
    box.appendChild(notes);
    var unlisted = clippedNode(round);
    if (unlisted) { box.appendChild(unlisted); }
    return box;
  }
  function firstOf(entries) {
    var list = Array.isArray(entries) ? entries.filter(function (entry) { return entry && typeof entry === "object"; }) : [];
    if (!list.length) { return null; }
    return String(list[0].label) + (list.length > 1 ? " (+" + (list.length - 1) + " more)" : "");
  }

  var box = node("div", "run-summary");
  var planReview = summary.planReview && typeof summary.planReview === "object" ? summary.planReview : null;
  if (planReview) {
    // A plan's review is unbounded: it ends when every lens approves, or when the operator ends it, with a reason.
    box.appendChild(node("p", "run-quiet", say("The plan's review has no round bound: it runs until every lens approves or the operator ends it.")));
    if (planReview.state === "ended") { box.appendChild(node("p", "run-ended", "Ended without alignment: " + (planReview.endedReason || "no reason recorded."))); }
  }
  else if (summary.observational) { box.appendChild(node("p", "run-quiet", "Recorded reviewer reports · display only")); }
  var rounds = Array.isArray(summary.roundDetail) ? summary.roundDetail.filter(function (round) { return round && typeof round === "object"; }) : [];
  var running = summary.running && typeof summary.running === "object" && typeof summary.running.round === "number"
    && typeof summary.running.head === "string" ? summary.running : null;
  if (running) {
    running = Object.assign({}, running, { lenses: Array.isArray(running.lenses) ? running.lenses : [],
                                           back: running.back && typeof running.back === "object" ? running.back : {} });
  }
  var key = typeof summary.runId === "string" ? summary.runId : (summary.observationKey || "");
  if (running) {
    // The present tense: the round under way, and the last finished round beneath it, dated by its candidate and how
    // far the branch has moved since.
    box.appendChild(node("p", "run-story", typeof running.headline === "string" && running.headline ? running.headline : "Round " + running.round + " under way"));
    var finished = rounds[rounds.length - 1];
    if (finished) {
      var parts = [nameOf(finished, rounds.length - 1) + " " + outcomeWords(finished)];
      if (typeof finished.candidateRef === "string" && finished.candidateRef) { parts.push(" ", [finished.candidateRef]); }
      if (typeof running.moved === "number") { parts.push(" · " + plural(running.moved, "commit") + " since"); }
      box.appendChild(line("run-last", parts));
    }
  } else {
    box.appendChild(node("p", "run-story", rounds.length ? story(rounds) : "No review rounds yet"));
  }
  // The redraw that finds the round it last showed in flight reduced settles that round's bead: for the one motion's
  // length, since a hover or a push redraws the panel again at once, and never after. It is kept by the story the page
  // names, since the card's own key changes when the finished rounds' evidence does.
  var flightKey = options && typeof options.subject === "string" ? options.subject : key;
  var lastNumber = rounds.length ? numberOf(rounds[rounds.length - 1], rounds.length - 1) : null;
  var flown = runInFlight[flightKey];
  if (running) { runInFlight[flightKey] = { round: running.round, settledAt: null }; }
  else if (flown && flown.settledAt === null && flown.round === lastNumber) { flown.settledAt = Date.now(); }
  else if (flown && flown.round !== lastNumber) { delete runInFlight[flightKey]; }
  var settling = !running && flown && flown.round === lastNumber && typeof flown.settledAt === "number"
    && Date.now() - flown.settledAt < RUN_SETTLE_MS;
  if (rounds.length || running) {
    var beads = rounds.length + (running ? 1 : 0);
    // While a round runs it is shown first; a reader's own pick of a finished round stands.
    var picked = typeof runRoundPicked[key] === "number" && runRoundPicked[key] < beads ? runRoundPicked[key] : beads - 1;
    var track = node("ol", "run-track");
    var buttons = [];
    var cardAt = function (index) { return index < rounds.length ? card(rounds[index], index) : runningCard(rounds[rounds.length - 1]); };
    // Picking a round updates the beads in place, so the button a reader pressed keeps keyboard focus.
    var choose = function (chosen) {
      runRoundPicked[key] = chosen;
      buttons.forEach(function (button, at) { button.setAttribute("aria-pressed", String(at === chosen)); });
      var next = cardAt(chosen);
      var from = runHeldHeight(shown);
      shown.replaceWith(next);
      shown = next;
      runEaseHeight(next, from);
    };
    rounds.forEach(function (round, index) {
      var made = bead(round, index, index ? rounds[index - 1] : null, index === picked, choose);
      if (settling && index === rounds.length - 1) { made.classList.add("is-settling"); }
      buttons.push(made.querySelector(".run-pick"));
      track.appendChild(made);
    });
    if (running) {
      var flight = runningBead(picked === rounds.length, function () { choose(rounds.length); });
      buttons.push(flight.querySelector(".run-pick"));
      track.appendChild(flight);
    }
    var shown = cardAt(picked);
    box.appendChild(track);
    box.appendChild(shown);
  }
  var waiting = firstOf(summary.waits);
  if (waiting) { box.appendChild(node("p", "run-live is-waiting", "Waiting on " + waiting)); }
  if (summary.open !== false) {
    var working = firstOf(summary.work) || (summary.activity && typeof summary.activity === "object" && typeof summary.activity.label === "string" ? summary.activity.label : null);
    if (working) { box.appendChild(node("p", "run-live", "Now: " + working)); }
  }
  // When the run ran and whether its evidence is recorded; the run's own id is the product's, not the reader's.
  var foot = node("p", "run-meta");
  function lead(words) { return foot.firstChild ? " · " + words : words.charAt(0).toUpperCase() + words.slice(1); }
  var started = when(summary.startedAt);
  var lastSeen = when(summary.lastAt);
  if (started) {
    foot.appendChild(node("span", "run-sep", lead(summary.open === true ? "running since " : "ran ")));
    foot.appendChild(timeNode("run-start", started, clock(started)));
    if (lastSeen && summary.open !== true) {
      foot.appendChild(node("span", "run-sep", " – "));
      // The same day shows the end's time alone; another day keeps the end's own day, "today" included.
      var ended = clock(lastSeen);
      if (started.toDateString() === lastSeen.toDateString()) { ended = ended.replace(/^(yesterday |[A-Z][a-z]{2} [0-9]+, )/, ""); }
      else if (lastSeen.toDateString() === new Date(Date.now()).toDateString()) { ended = "today " + ended; }
      foot.appendChild(timeNode("run-end", lastSeen, ended));
    }
  }
  if (summary.status === "complete") { foot.appendChild(node("span", "run-sep", lead("evidence recorded"))); }
  if (foot.firstChild) { box.appendChild(foot); }
  return box;
}
