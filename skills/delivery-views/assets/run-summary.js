// The round a reader picked in each run, kept across live redraws.
var runRoundPicked = {};
// How a height change moves, by the repository's motion guidance (emil-design-eng): a morph on screen eases in and out
// on a strong curve, stays within 300ms (longer only as far as it travels), and never runs for a keyboard action, which a
// reader repeats too often to wait on.
var RUN_EASING = "cubic-bezier(.77, 0, .175, 1)";
function runEaseDuration(from, to) { return Math.round(240 + Math.min(Math.abs(to - from), 600) / 600 * 60); }
var runLastInput = "pointer";
if (typeof document !== "undefined") {
  document.addEventListener("keydown", function () { runLastInput = "keyboard"; }, true);
  document.addEventListener("pointerdown", function () { runLastInput = "pointer"; }, true);
}
function runMotionWanted(node) {
  return typeof node.animate === "function" && runLastInput !== "keyboard"
    && !(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
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
function runSummaryNode(summary, options) {
  var SEVERITIES = ["P0", "P1", "P2", "P3"];
  var WORDS = { P0: "critical", P1: "serious", P2: "moderate", P3: "minor" };
  var PIPS_SHOWN = 8;
  function node(tag, className, value) {
    var made = document.createElement(tag);
    if (className) { made.className = className; }
    if (value !== undefined && value !== null) { made.textContent = String(value); }
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
  // The shipped icons, drawn from SVG elements (never markup) as Tabler's outline icons (MIT) draw them. The
  // configuration names a lens's icon and name; a lens with no icon gets a round badge with its initial.
  var ICONS = {
    bullseye: ["M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0", "M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0 -10 0",
      "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0"],
    spy: ["M3 11h18", "M5 11v-4a3 3 0 0 1 3 -3h8a3 3 0 0 1 3 3v4", "M7 17m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0",
      "M17 17m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0", "M10 17h4"],
    shield: ["M12 3a12 12 0 0 0 8.5 3a12 12 0 0 1 -8.5 15a12 12 0 0 1 -8.5 -15a12 12 0 0 0 8.5 -3"],
    bug: ["M9 9v-1a3 3 0 0 1 6 0v1", "M8 9h8a6 6 0 0 1 1 3v3a5 5 0 0 1 -10 0v-3a6 6 0 0 1 1 -3", "M3 13l4 0", "M17 13l4 0",
      "M12 20l0 -6", "M4 19l3.35 -2", "M20 19l-3.35 -2", "M4 7l3.75 2.4", "M20 7l-3.75 2.4"],
    gauge: ["M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0", "M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0", "M13.41 10.59l2.59 -2.59",
      "M7 12a5 5 0 0 1 5 -5"],
    lock: ["M5 13a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6z", "M11 16a1 1 0 1 0 2 0a1 1 0 0 0 -2 0",
      "M8 11v-4a4 4 0 1 1 8 0v4"],
    book: ["M3 19a9 9 0 0 1 9 0a9 9 0 0 1 9 0", "M3 6a9 9 0 0 1 9 0a9 9 0 0 1 9 0", "M3 6l0 13", "M12 6l0 13", "M21 6l0 13"]
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
    parts.forEach(function (d) {
      var shape = document.createElementNS(svgNs, "path");
      shape.setAttribute("d", d);
      svg.appendChild(shape);
    });
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
    if (last.outcome === "approve") { head = number === 1 ? "Approved on the first round" : "Approved on round " + number; }
    else if (last.outcome === "changes") { head = "Round " + number + " asked for changes"; }
    else { head = rounds.length + (rounds.length === 1 ? " review round" : " review rounds"); }
    var raised = noteWords(total);
    var tail = !reported ? "findings not reported" : raised ? raised + " raised" : "nothing raised";
    return head + " · " + tail;
  }
  function outcomeOf(round) { return round.outcome === "approve" || round.outcome === "changes" ? round.outcome : "unknown"; }
  // "approved", "asked for changes", "reported as <outcome>" for any other outcome, or "outcome not reported".
  function outcomeWords(round) {
    var outcome = outcomeOf(round);
    if (outcome === "approve") { return "approved"; }
    if (outcome === "changes") { return "asked for changes"; }
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
  function verdictWords(kind, verdict) { return kind === "approve" ? "approved" : kind === "changes" ? "asked for changes" : String(verdict); }
  // A lens's icon and name; its verdict is said only where it differs from the round's, which the header gives.
  function reviewerHead(className, lens, verdict, outcome) {
    var kind = verdict === undefined ? "unknown" : verdictKind(verdict);
    var head = node("span", (className ? className + " " : "") + "run-lens is-" + kind);
    head.appendChild(lensIcon(lens));
    head.appendChild(node("b", "run-lens-name", lensLabel(lens)));
    if (verdict !== undefined && kind !== outcome) { head.appendChild(node("span", "run-lens-verdict", verdictWords(kind, verdict))); }
    return head;
  }
  function isNote(entry) { return entry && typeof entry === "object" && typeof entry.id === "string" && typeof entry.lens === "string" && typeof entry.summary === "string"; }
  function short(commit) { return typeof commit === "string" ? commit.slice(0, 7) : null; }
  // What later rounds said became of each finding, by its id: the first round to settle it wins.
  function fates(index) {
    // Keyed by finding id, with no inherited names, so an id like "constructor" finds nothing it did not set.
    var known = Object.create(null);
    rounds.slice(index + 1).forEach(function (later, offset) {
      (Array.isArray(later.settled) ? later.settled : []).forEach(function (entry) {
        if (entry && typeof entry.id === "string" && !known[entry.id]) { known[entry.id] = { entry: entry, round: numberOf(later, index + 1 + offset) }; }
      });
    });
    return known;
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
  // A finding's state, as the run last observed it: a quiet check when a later round confirmed it fixed,
  // a faint ring while it is open, and any other state in words.
  function stateNode(entry, fate) {
    var fixed = entry.state === "resolved" && fate && fate.entry.state === "fixed";
    var words = fixed ? "\u2713 fixed" : entry.state === "unresolved" ? "open"
      : entry.state === "deferred" ? "deferred" + (entry.deferredIssue || (fate && fate.entry.deferredIssue) ? " · " + (entry.deferredIssue || fate.entry.deferredIssue) : "")
      : entry.state === "resolved" && fate ? String(fate.entry.state) : String(entry.state);
    var kind = fixed ? "fixed" : entry.state === "unresolved" ? "open" : "other";
    var made = node("span", "run-fate is-" + kind, words);
    return made;
  }
  // What became of the round's findings, said once: "All 5 fixed in 61f086d · confirmed round 2".
  function outcomeOfNotes(raised, known) {
    var fixed = raised.filter(function (entry) { return entry.state === "resolved" && known[entry.id] && known[entry.id].entry.state === "fixed"; });
    var open = raised.filter(function (entry) { return entry.state === "unresolved"; }).length;
    if (!fixed.length) {
      if (!open) { return null; }
      return node("p", "run-quiet", open === raised.length ? (open === 1 ? "Still open" : "All " + open + " still open") : open + " open");
    }
    var confirmed = [];
    fixed.forEach(function (entry) { if (confirmed.indexOf(known[entry.id].round) < 0) { confirmed.push(known[entry.id].round); } });
    var commits = commitsOf(fixed.map(function (entry) { return known[entry.id].entry; }));
    var head = fixed.length === raised.length ? (raised.length === 1 ? "Fixed" : "All " + raised.length + " fixed") : fixed.length + " of " + raised.length + " fixed";
    return line("run-settled", [head + (commits.length ? " in " : ""), commits,
      " · confirmed round " + confirmed.sort(function (a, b) { return a - b; }).join(", ") + (open ? " · " + open + " open" : "")]);
  }
  // Earlier findings this round confirmed, as one line: "Confirmed 5 earlier notes fixed in 61f086d".
  function settledNode(settled) {
    var fixed = settled.filter(function (entry) { return entry && entry.state === "fixed"; });
    var others = settled.length - fixed.length;
    var parts = ["Confirmed "];
    if (fixed.length) {
      var commits = commitsOf(fixed);
      parts.push(fixed.length + (fixed.length === 1 ? " earlier note" : " earlier notes") + " fixed" + (commits.length ? " in " : ""), commits);
    }
    if (others) { parts.push((fixed.length ? ", " : "") + others + (others === 1 ? " earlier note" : " earlier notes") + " settled otherwise"); }
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
  // The picked round in full. With the round's report, each reviewer lists what it raised and what became
  // of it; without one, the lenses' verdicts and the round's counts.
  function card(round, index) {
    var outcome = outcomeOf(round);
    var box = node("div", "run-card is-" + outcome);
    var head = node("p", "run-card-head");
    if (summary.observational && typeof round.candidateRef === "string") {
      var candidate = node("p", "run-quiet", "Reviewed candidate " + short(round.candidateRef));
      candidate.setAttribute("aria-label", "Reviewed candidate " + round.candidateRef);
      box.appendChild(candidate);
    }
    head.appendChild(node("b", null, nameOf(round, index)));
    var pill = outcome === "approve" ? "Approved" : outcome === "changes" ? "Changes requested" : outcomeWords(round);
    head.appendChild(node("span", "run-card-outcome", pill.charAt(0).toUpperCase() + pill.slice(1)));
    box.appendChild(head);
    var verdicts = round.verdicts && typeof round.verdicts === "object" ? round.verdicts : {};
    if (Array.isArray(round.raised) && !isFold(round)) {
      var raised = round.raised.filter(isNote);
      var lenses = Object.keys(verdicts);
      raised.forEach(function (entry) { if (lenses.indexOf(entry.lens) < 0) { lenses.push(entry.lens); } });
      Object.keys(briefsOf(round) || {}).forEach(function (lens) { if (lenses.indexOf(lens) < 0) { lenses.push(lens); } });
      var known = fates(index);
      // When every finding is fixed, or every one is still open, the footer says so once and the lines carry no mark.
      var fixedAll = raised.length && raised.every(function (entry) { return entry.state === "resolved" && known[entry.id] && known[entry.id].entry.state === "fixed"; });
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
        group.appendChild(top);
        var record = recordNode(round, lens, false);
        if (record) { group.appendChild(record); }
        if (own.length) {
          // Each severity is said once, as a quiet band heading over its findings, worst first; a minor
          // finding's text is lighter than a worse one's.
          var list = node("ul", "run-findings");
          var band = null;
          own.forEach(function (entry) {
            var severity = SEVERITIES.indexOf(entry.severity) < 0 ? "P3" : entry.severity;
            if (severity !== band) {
              band = severity;
              var size = own.filter(function (other) { return (SEVERITIES.indexOf(other.severity) < 0 ? "P3" : other.severity) === severity; }).length;
              var name = WORDS[severity];
              list.appendChild(node("li", "run-band is-" + severity.toLowerCase(), name.charAt(0).toUpperCase() + name.slice(1) + (size > 1 ? " · " + size : "")));
            }
            var item = node("li", "run-finding is-" + severity.toLowerCase());
            // Summaries are sentences; one stored without its full stop gets one here.
            var said = node("span", "run-finding-text", entry.summary + (/[.!?\u2026:)"'\u201D]$/.test(entry.summary) ? "" : "."));
            item.appendChild(said);
            if (!uniform) { item.appendChild(stateNode(entry, known[entry.id])); }
            list.appendChild(item);
          });
          group.appendChild(list);
        }
        box.appendChild(group);
      });
      var told = outcomeOfNotes(raised, known);
      if (told) { box.appendChild(told); }
      var settled = Array.isArray(round.settled) ? round.settled : [];
      if (settled.length) { box.appendChild(settledNode(settled)); }
      var closures = settled.filter(function (entry) { return summary.observational && typeof entry.resolution === "string"; });
      var closureLenses = [];
      closures.forEach(function (entry) { if (closureLenses.indexOf(entry.lens) < 0) { closureLenses.push(entry.lens); } });
      closureLenses.sort().forEach(function (lens) {
        var group = node("div", "run-reviewer run-closure-reviewer");
        group.appendChild(reviewerHead("run-reviewer-head", lens, verdictOf(verdicts, lens), outcome));
        var list = node("ul", "run-findings");
        closures.filter(function (entry) { return entry.lens === lens; }).forEach(function (entry) {
          var item = node("li", "");
          var disclosure = node("details", "run-closures");
          var preview = node("summary", "run-finding");
          preview.appendChild(node("span", "run-finding-text", entry.resolution));
          preview.appendChild(node("span", "run-fate", entry.state === "fixed" ? "fixed" : entry.state === "dropped" ? "withdrawn" : entry.state));
          disclosure.appendChild(preview);
          disclosure.appendChild(node("p", "run-closure", entry.id + " · " + entry.resolution));
          item.appendChild(disclosure);
          list.appendChild(item);
        });
        group.appendChild(list);
        box.appendChild(group);
      });
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
    box.appendChild(node("p", "run-quiet", "The plan's review has no round bound: it runs until every lens approves or the operator ends it."));
    if (planReview.state === "ended") { box.appendChild(node("p", "run-ended", "Ended without alignment: " + (planReview.endedReason || "no reason recorded."))); }
  }
  else if (summary.observational) { box.appendChild(node("p", "run-quiet", "Recorded reviewer reports · display only")); }
  var rounds = Array.isArray(summary.roundDetail) ? summary.roundDetail.filter(function (round) { return round && typeof round === "object"; }) : [];
  box.appendChild(node("p", "run-story", rounds.length ? story(rounds) : "No review rounds yet"));
  if (rounds.length) {
    var key = typeof summary.runId === "string" ? summary.runId : (summary.observationKey || "");
    var picked = typeof runRoundPicked[key] === "number" && runRoundPicked[key] < rounds.length ? runRoundPicked[key] : rounds.length - 1;
    var track = node("ol", "run-track");
    var buttons = [];
    // Picking a round updates the beads in place, so the button a reader pressed keeps keyboard focus.
    rounds.forEach(function (round, index) {
      var made = bead(round, index, index ? rounds[index - 1] : null, index === picked, function (chosen) {
        runRoundPicked[key] = chosen;
        buttons.forEach(function (button, at) { button.setAttribute("aria-pressed", String(at === chosen)); });
        var next = card(rounds[chosen], chosen);
        var from = runHeldHeight(shown);
        shown.replaceWith(next);
        shown = next;
        runEaseHeight(next, from);
      });
      buttons.push(made.querySelector(".run-pick"));
      track.appendChild(made);
    });
    var shown = card(rounds[picked], picked);
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
