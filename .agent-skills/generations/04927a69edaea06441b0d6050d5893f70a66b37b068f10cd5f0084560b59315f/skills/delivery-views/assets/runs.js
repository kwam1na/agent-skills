// The runs page: every run, open ones first under Now, then the ended ones, each newest first. Each run's line draws its
// workflow stages in the order it first entered them, the current one ringed; its panel opens in place, through the page
// controller's one fold (page.js). A live page reads itself again whenever the server says the runs moved, and redraws
// through the controller, which keeps the reader's place, what they opened and what they focused.
(function () {
  "use strict";
  function read(id, fallback) {
    try { return JSON.parse(document.getElementById(id).textContent) || fallback; } catch (error) { return fallback; }
  }
  var record = read("delivery-run-page", { runs: [], notes: ["The runs could not be read."] });
  var VIEW = read("delivery-run-page-view", {});
  var app = document.getElementById("app");
  var CHAPTER = "runs";
  var served = typeof location === "object" && /^https?:$/.test(location.protocol);

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
  function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function at(value) { return typeof value === "string" && value.length >= 16 ? value.slice(0, 10) + " " + value.slice(11, 16) : (text(value) || "—"); }
  function milliseconds(total) {
    if (typeof total !== "number" || !isFinite(total) || total < 0) { return "—"; }
    if (total < 1000) { return Math.round(total) + "ms"; }
    var s = Math.round(total / 1000);
    if (s < 60) { return s + "s"; }
    return Math.floor(s / 60) + "m " + (s % 60) + "s";
  }
  function viewHref(output) {
    // Each path segment is encoded and the separators kept, as the live server names a view by its output.
    return (served ? "/view/" : "") + output.split("/").map(encodeURIComponent).join("/");
  }
  function link(words, output, kind, id) {
    var node = el("a", null, words);
    node.setAttribute("href", viewHref(output));
    node.setAttribute("data-key", page.key(kind, id));
    return node;
  }
  function safeHref(value) { return /^https?:\/\//.test(value) ? value : null; }

  var STAGE_WORDS = { route: "Route", plan: "Plan", diagnose: "Diagnose", execute: "Execute", review: "Review", compound: "Compound" };

  function lineNode(run) {
    var line = el("ol", "stages");
    line.setAttribute("aria-label", "Workflow stages");
    list(run.stages).forEach(function (stage) {
      var station = el("li", "stage" + (stage === run.currentStage ? " current" : ""));
      station.setAttribute("data-stage", stage);
      if (stage === run.currentStage) { station.setAttribute("aria-current", "step"); }
      add(station, el("i", "dot"), el("span", "stage-name", STAGE_WORDS[stage] || stage));
      line.appendChild(station);
    });
    return line;
  }

  function waitNode(wait) {
    var node = el("p", "wait" + (wait.needsYou ? " needs" : ""));
    if (wait.needsYou) { node.appendChild(el("strong", "needs-you", "Needs you")); }
    else { node.appendChild(el("span", "waiting-on", "Waiting on " + (text(wait.waitingOn) || "unknown"))); }
    add(node, " ", text(wait.reason));
    if (text(wait.nextAction)) { add(node, " ", el("span", "next", "Next: " + wait.nextAction)); }
    return node;
  }

  function endedWords(run) {
    if (text(run.displacedBy)) { return "Left open, replaced"; }
    if (isObject(run.result) && text(run.result.result)) { return "Ended " + run.result.result; }
    return run.open === true ? "Open" : "Ended";
  }

  function section(title, rows) { return add(el("section", "group"), el("h2", null, title), rows); }

  function detailNode(run) {
    var box = el("div", "run-detail");
    var gates = list(run.gates);
    box.appendChild(section("Gates", gates.length ? add.apply(null, [el("ul", "gates")].concat(gates.map(function (gate) {
      return add(el("li", "gate"), el("code", null, text(gate.command)), " ", el("span", "outcome " + text(gate.outcome), text(gate.outcome) || "unreported"),
        " ", el("span", "when", milliseconds(gate.durationMs)));
    }))) : el("p", "say", "No gate reported.")));
    var decisions = list(run.decisions);
    box.appendChild(section("Decisions", decisions.length ? add.apply(null, [el("ul", "decisions")].concat(decisions.map(function (decision) {
      var said = text(decision.fork) ? decision.fork + ": " + text(decision.choice) : text(decision.text) || text(decision.choice);
      return add(el("li"), said, text(decision.why) ? el("span", "why", " " + decision.why) : null);
    }))) : el("p", "say", "No decision recorded.")));
    var blockers = list(run.blockers);
    box.appendChild(section("Blockers", blockers.length ? add.apply(null, [el("ul", "blockers")].concat(blockers.map(function (blocker) {
      return add(el("li"), text(blocker.summary), text(blocker.code) ? el("code", "code", " " + blocker.code) : null);
    }))) : el("p", "say", "No blocker recorded.")));
    var change = text(run.changeRequest);
    var known = isObject(VIEW.links) && isObject(VIEW.links.changes) ? VIEW.links.changes[change] : null;
    var href = isObject(known) ? safeHref(text(known.href)) : safeHref(change);
    var changeNode = el("p", "change");
    if (!change) { changeNode.textContent = "No change request opened."; }
    else if (href) {
      var anchor = el("a", null, isObject(known) ? text(known.label) : change);
      anchor.setAttribute("href", href);
      anchor.setAttribute("data-key", page.key("change", text(run.runId)));
      changeNode.appendChild(anchor);
    }
    else { changeNode.textContent = change; }
    box.appendChild(section("Change request", changeNode));
    var summary = isObject(run.summary) ? run.summary : null;
    if (summary && typeof runSummaryNode === "function") {
      box.appendChild(section("Review", runSummaryNode(summary, { lenses: VIEW.lenses })));
    }
    var result = isObject(run.result) ? run.result : null;
    var resultNode = el("p", "result");
    if (!result) { resultNode.textContent = run.open === true && run.section === "now" ? "Still running." : "No result recorded."; }
    else {
      var cost = typeof result.total === "number" ? result.total + " " + (text(result.unit) || "units") : "cost unreported";
      resultNode.textContent = (text(result.result) || "ended") + " · " + cost + " · coverage " + (text(result.coverage) || "unreported");
    }
    box.appendChild(section("Result", resultNode));
    if (run.section === "ended" && text(VIEW.history)) { box.appendChild(add(el("p", "history-link"), link("In the delivery history", VIEW.history, "history", text(run.runId)))); }
    return box;
  }

  function runNode(run) {
    var item = el("article", "run" + (run.needsYou ? " needs" : ""));
    item.setAttribute("data-run", text(run.runId));
    // Its id is its run's, so a link to the run opens its panel.
    if (text(run.runId)) { item.setAttribute("id", run.runId); }
    item.setAttribute("data-section", text(run.section));
    var button = el("button", "run-head");
    button.setAttribute("type", "button");
    var name = add(el("span", "run-name"), el("b", null, text(run.ticket) || text(run.runId)),
      text(run.ticket) ? el("small", null, text(run.runId)) : null);
    add(button, name);
    if (run.unavailable === true) { button.appendChild(el("span", "note", "This run could not be read.")); }
    else if (run.clipped === true) { button.appendChild(el("span", "note", "Its details are over the page's size budget.")); }
    else {
      button.appendChild(lineNode(run));
      var doing = isObject(run.activity) ? run.activity : null;
      if (run.section === "now") {
        button.appendChild(el("p", "doing", doing ? (text(doing.nextStep) || text(doing.phase) || text(doing.activityId)) : "No activity running."));
      }
    }
    button.appendChild(el("span", "state", run.section === "now" ? "Started " + at(run.startedAt) : endedWords(run) + " · " + at(run.lastAt || run.startedAt)));
    item.appendChild(button);
    if (run.section === "now") { list(run.waits).forEach(function (wait) { if (isObject(wait)) { item.appendChild(waitNode(wait)); } }); }
    if (text(run.piece)) {
      item.appendChild(add(el("p", "piece"), "Piece " + run.piece + " of this delivery", text(VIEW.lineMap) ? " · " : null,
        text(VIEW.lineMap) ? link("on the line map", VIEW.lineMap, "piece", text(run.runId)) : null));
    }
    var openable = run.unavailable !== true && run.clipped !== true && text(run.runId);
    if (openable) {
      // The page's one fold: shut until the reader opens it, kept open for the visit across every redraw.
      var detail = detailNode(run);
      item.appendChild(detail);
      page.fold({ button: button, region: detail }, { key: page.key("run", run.runId), of: item });
    } else {
      button.setAttribute("data-key", page.key("run", text(run.runId) || text(run.ticket) || "unread"));
    }
    return item;
  }

  // The plans being reviewed before any delivery, each served by a preview server of its own: one sentence, then one
  // sheet listing each plan by its title, its plan file, and its local and hosted links. Drawn only when the live
  // server named one.
  function previewsNode(previews) {
    var box = add(el("section", "previews"), el("h2", null, "Plans in review"),
      el("p", "say", previews.length === 1 ? "A plan is being reviewed by agents before any delivery takes it up; it has its own live view."
        : "These plans are being reviewed by agents before any delivery takes them up; each has its own live view."));
    box.setAttribute("id", "previews");
    var sheet = el("ul", "preview-sheet");
    previews.forEach(function (preview) {
      var row = el("li", "preview");
      row.setAttribute("data-plan", text(preview.plan));
      var name = text(preview.title) || text(preview.plan);
      var title = el("b");
      (typeof richWords === "function" ? richWords(name) : [document.createTextNode(name)]).forEach(function (node) { title.appendChild(node); });
      add(row, add(el("span", "preview-name"), title, text(preview.title) ? el("small", null, text(preview.plan)) : null));
      // Named as the reader sees the title, without the marks that set its code apart.
      var said = title.textContent;
      var links = el("span", "preview-links");
      var live = safeHref(text(preview.live));
      if (live) {
        var local = el("a", "preview-live", "Live view");
        local.setAttribute("href", live);
        local.setAttribute("aria-label", "Live view of " + said);
        local.setAttribute("data-key", page.key("preview-live", text(preview.plan) || live));
        links.appendChild(local);
      }
      var hosted = /^https:\/\//.test(text(preview.hosted)) ? preview.hosted : null;
      if (hosted) {
        var away = el("a", "preview-hosted", "Hosted view");
        away.setAttribute("href", hosted);
        away.setAttribute("aria-label", "Hosted view of " + said);
        away.setAttribute("data-key", page.key("preview-hosted", text(preview.plan) || hosted));
        links.appendChild(away);
      }
      row.appendChild(links);
      sheet.appendChild(row);
    });
    box.appendChild(sheet);
    return box;
  }

  function draw() {
    var kicker = add(el("div", "kicker"), el("span", null, "Runs"),
      text(VIEW.lineMap) ? link("The line map", VIEW.lineMap, "view", VIEW.lineMap) : null,
      text(VIEW.history) ? link("The delivery history", VIEW.history, "view", VIEW.history) : null);
    var say = VIEW.runs === true ? "Every run of every workflow in this repository, as of " + at(VIEW.asOf) + " UTC."
      : "No runs command is configured.";
    var top = add(el("div", "page"), kicker, el("h1", "title", "What every workflow is doing."), el("p", "say", say));
    // The page's one chapter, named for itself.
    top.setAttribute("id", CHAPTER);
    if (VIEW.watched === "unwatched") {
      top.appendChild(el("p", "unwatched", "Runs aren't being watched: the live server could not read the run store, so this page shows them as of its last render."));
    }
    var notes = list(record.notes);
    if (notes.length) { top.appendChild(add.apply(null, [el("ul", "notes")].concat(notes.map(function (note) { return el("li", null, text(note)); })))); }
    var previews = list(VIEW.previews).filter(isObject);
    if (previews.length) { top.appendChild(previewsNode(previews)); }
    var runs = list(record.runs).filter(isObject);
    var now = runs.filter(function (run) { return run.section === "now"; });
    var ended = runs.filter(function (run) { return run.section !== "now"; });
    var nowBox = add(el("section", "now"), el("h2", null, "Now"));
    nowBox.setAttribute("id", "now");
    if (!now.length) { nowBox.appendChild(el("p", "say", "No run is open.")); }
    now.forEach(function (run) { nowBox.appendChild(runNode(run)); });
    var endedBox = add(el("section", "ended"), el("h2", null, "Ended"));
    endedBox.setAttribute("id", "ended");
    if (!ended.length) { endedBox.appendChild(el("p", "say", "No run has ended yet.")); }
    ended.forEach(function (run) { endedBox.appendChild(runNode(run)); });
    app.replaceChildren(add(top, nowBox, endedBox));
  }

  // Read again on the server's word, as `why` names it: a pushed record or moved runs.
  function refetch(why) {
    if (!served || typeof fetch !== "function") { return; }
    // Its own address, never the one it was opened at: served as the front page while there was no record, the front
    // page becomes the line map once a record reads, and this page would never find itself there again.
    fetch(text(VIEW.own) ? "/view/" + VIEW.own.split("/").map(encodeURIComponent).join("/") : location.href, { cache: "no-store" }).then(function (answer) { return answer.text(); }).then(function (body) {
      var found = /<script type="application\/json" id="delivery-run-page">([^<]*)<\/script>/.exec(body);
      var seen = /<script type="application\/json" id="delivery-run-page-view">([^<]*)<\/script>/.exec(body);
      if (!found) { return; }
      var next = JSON.parse(found[1]);
      if (!isObject(next)) { return; }
      record = next;
      if (seen) { VIEW = JSON.parse(seen[1]) || VIEW; }
      page.redraw(draw, why);
    }).catch(function () { /* the runs already shown stay */ });
  }

  page.redraw(draw, "first");
  // A one-chapter page: a link to Now, Ended or a run opens there through the controller.
  page.start({ chapters: function () { return [CHAPTER]; }, show: function () {} });
  var store = globalThis.deliveryStateStore;
  if (isObject(store)) {
    if (typeof store.onRuns === "function") { try { store.onRuns(function () { refetch("runs"); }); } catch (error) { /* the page stays as rendered */ } }
    if (typeof store.subscribe === "function") { try { store.subscribe(function () { refetch("push"); }); } catch (error) { /* likewise */ } }
  }
})();
