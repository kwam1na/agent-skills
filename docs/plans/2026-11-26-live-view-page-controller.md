---
title: One controlled layer for the delivery views' navigation and page mechanics - Plan
type: refactor
date: 2026-11-26
topic: live-view-page-controller
reviewSlug: live-view-page-controller
execution: code
status: draft
---

# One controlled layer for the delivery views' navigation and page mechanics - Plan

## Goal

Every delivery view (the line map, the runs page and the history page, local and
hosted) has one owner for where the reader is, how the page scrolls, what stays
open and where focus goes. A redraw, whatever starts it, leaves the reader where
they were, with the same folds open and the same control focused. A page turn, a
deep link and an in-page link each move the page once, by one rule, and Back
returns to the chapter the reader came from. Hovering never changes what is
selected. A future feature reaches these mechanics only through the layer's
public calls.

This plan answers epic V26-4004. It changes nothing by itself: the stories below
are created as tracked items under V26-4004 once the plan is approved. Slice 1
is V26-4003, which already exists; it keeps its criteria and gains one
dependency (decision 1).

## Scope

- In:
  - one page controller in a new shared asset,
    `skills/delivery-views/assets/page.js`, loaded first by every delivery view
    and by the hosted build (decision 1), with the epic's five parts: location
    and `go(location, reason)`, scroll by reason, the redraw transaction, one
    keyed UI-state store with one fold primitive, and focus by key;
  - one motion check, in `page.js`, read by every asset;
  - the line map (`line-map.js`), the folds and disclosures `run-summary.js`
    draws, the runs page (`runs.js`) and the history page (`history.js`), each
    brought onto the controller;
  - the hosted viewer's anchored patches in
    `hosted/delivery-viewer/scripts/build-assets.mjs` that keep scroll or build
    the plan chapter by hand;
  - the bugs the inventory found: hover changes the persistent selection, deep
    links don't open the folds holding their target, the hosted plan chapter
    skips the page's observers, Happened re-eases its height on every redraw,
    the local line map draws twice for each push or tick, and the runs page loses
    the reader's place and focus on every push;
  - the page rules in `skills/delivery-views/SKILL.md` those changes alter, and
    a new "Extending the page" section there (decision 11).
- Out:
  - what any page says or draws: words, marks, order and layout stay as they
    are, so the text goldens hold (see "Goldens");
  - the theme switch and its stored pick (`line-map.js` lines 1 to 55), already
    one store with one key;
  - the launcher's own list scroll, a scroll inside a fixed overlay, not the
    page;
  - the hosted viewer's own shell, `viewer.ts`, beyond a comment and what a
    removed patch needs;
  - the shared-seat words V26-3993 is changing.

## Decisions recorded here

The operator aligned on the design on 2026-11-26 (the epic's description), and
the same day widened it to every delivery view and chose a shared asset for it.
These are the calls the plan makes; the operator confirms or overturns them in
approving it.

1. **The controller is its own shared asset, `page.js`.** This overturns the
   epic's "a bounded section of `line-map.js`, not a new asset file", by the
   operator's choice on 2026-11-26, so the runs and history pages share it.
   `render.py` loads it first on all three pages, before `run-summary.js`, and
   `build-assets.mjs` puts it first in the hosted renderer's input. It ships as
   a `delivery-views` output: the four profiles that list the delivery-views
   assets, `provenance.lock.json` and the release-integration pin of the core
   payload gain it. A first story (SLICE-0) makes the asset and its wiring, so
   V26-4003 builds its transaction there; V26-4003 is blocked by SLICE-0.
2. **Page turns push; everything else replaces.** A turn to another chapter in
   paged layout (rail, chapter tab, corner name, arrows, scroll-past, launcher,
   an in-page link to another chapter) adds one history entry. Following the
   scroll in the one-page layout, a redraw, the first route, the layout switch,
   an outside address, Back and Forward, and an item link within the chapter
   already shown add none. In the one-page layout nothing the controller does
   adds an entry: every chapter is on the one page, so Back leaves it. An outside
   address change adds the browser's own entry and no second one. (Epic
   decision, made exact.)
3. **One entry for every address change.** One delegated click handler on
   `document` takes a same-page `#id` link whose target is a chapter or lies
   inside one, wherever the link sits (the rail and chapters in `#app`, the
   chapter tab and the corner name in `.corner` and `body`), cancels the
   browser's jump and calls `go`. Any other `#id` link, such as the hosted
   shell's skip link to `#app`, and every link to another document are left to
   the browser. `popstate` and
   `hashchange` both go to one routing entry, which does nothing when the
   address already equals `page.here()`, so a Back that fires both events, or an
   address the controller wrote itself, routes once. `location.hash`,
   `pushState` and `replaceState` are written only by the controller.
4. **Deep links open their folds in slice 3, not slice 2.** The epic puts
   "deep links that open their folds" in slice 2. Keeping a fold open through a
   redraw needs the one fold primitive, which arrives in slice 3; done earlier it
   would need a special case per mechanism, which slice 3 then deletes. Slice 2
   resolves item and chapter and scrolls once; slice 3 adds each fold holding the
   item, opened.
5. **`keepPlace` and the line's sideways keeper go in slice 2.** V26-4003 keeps
   the plan chapter's `keepPlace` (its scope, item 3) and removes the hosted
   `hostedY` patch (item 2). Slice 2 retires `keepPlace` into the redraw anchor
   and `prev.lineScroll` into `page.sideways`, because "only the controller
   writes the scroll" is slice 2's rule.
6. **Hover names and lights; it never selects.** Hovering a stop, a tick or a
   step names it and lights its lane, as today. It no longer opens the detail
   panel or changes `selected`. Click, Enter, Space and keyboard focus select.
   The test that pins today's behaviour ("keeps a stop chosen by pointing at it
   through an update", `line-map.test.mjs` 1134) is re-derived, not deleted.
7. **Keys come from what the record names.** A `data-key` is `page.key(kind,
   id)`: a kind of control and the record's own id for it (`stop:PAY-2358`,
   `fold:plan:units`, `decision:3`, `pager:checks`, `run:<runId>`), never its
   position. An item the record names only by its words is keyed by those words
   (`aside:<words>`, as `asideOpen` does today), since they are its identity.
   Two controls share a key only when they stand for the same thing in two
   places (a ticket under its stop and in the detail); focus then returns to the
   one in the same region.
8. **Saved state keeps today's keys and formats.** `LAYOUT_KEY` (`paged` or
   `scroll`), `DEPTH_KEY` (a depth name) and `FOLD_KEY` (a JSON array of open
   fold names) are read and written exactly as today, so a reader's remembered
   layout, depth and plan folds survive the upgrade. Everything else is kept in
   memory for the visit, as today.
9. **One motion check with two questions.** `page.motion(fromReader)`: reduced
   motion always refuses; the keyboard clause (the last input was a key) refuses
   only when `fromReader` is true, for motion the reader's own action starts (a
   scroll, a fold, the panel swap). Live-update motion (a landing tick, a digit
   flip, a fresh beat) asks with `fromReader` false, so a keyboard reader still
   sees a push land, as today. `reduced()` and `runMotionWanted` become calls to
   it. The CSS `html { scroll-behavior: smooth }` rule and its reduced-motion
   override go, keeping that override's `.lines-track [data-lane] { transition:
   none; }`, so a scroll moves smoothly only when the controller asks.
10. **A refresh redraws only on a change.** A push or tick draws at once, as
    today. The line map's runs refresh three seconds later reads seven blocks
    (runs, links, reviews, needs, files, left-open, integration) and redraws only
    when any of them differs from what is in use. Redraws asked for within one
    animation frame merge into one (V26-4003).
11. **The controller is the views' extension point.** A feature that moves the
    page, scrolls, keeps something open or selected, or takes focus does it
    through the controller's public calls, never beside them. Each kind of
    navigation is a declared reason, so a new kind is added by declaring one, not
    by editing `go`. `SKILL.md` says how to add each, a page test adds one of each
    from outside the controller, and the source pins name the calls in their
    failure messages.
12. **Disclosures are folds too.** The one fold primitive takes either a
    `details` element or a disclosure (a button with `aria-expanded` over a
    region it hides). The run summary's deferral rows and the runs and history
    pages' run rows keep their button and `hidden` region, so the tests that pin
    those (`run-summary-findings` 208 and 307, the runs page's panel tests) stay
    as they are; only their open state and motion move onto the primitive.

## Inventory

As read on `main` at `a80ceb99`. Line numbers will move when V26-3996 lands;
each story re-reads its sites at its own base.

### Scroll and address writes (`line-map.js`)

| Line | Site | Becomes |
| --- | --- | --- |
| 617 | layout switch, one-page: section `scrollIntoView` | `go({chapter}, "layout")` |
| 677-683 | `showPage`: target centred, land at end, top, one-page target | the reasons' scroll rules |
| 871 | launcher page command, one-page: section `scrollIntoView` | `go({chapter}, "turn")` |
| 3383 | line's sideways `scrollLeft` from `prev.lineScroll` | `page.sideways` |
| 4234 | What's left row: reveal, then smooth section scroll | `go({chapter: "line", item}, "link")` |
| 4745 | `openDecision`: smooth centre, then `summary.focus()` | `go(…, "link")`, focus kept with `preventScroll` |
| 5143 | `keepPlace` | the redraw anchor |
| 5316 | plan unit button: reveal, centre, `focus({preventScroll})` | `go({chapter: "plan", item}, "link")` |
| 5665 | setback link: `target.focus()`, reveal, nearest | `go(…, "link")`, focus kept with `preventScroll` |
| 796 | launcher list row, nearest | unchanged (inside the launcher) |
| 649, 869, 936 | `location.hash =`: arrows, launcher, scroll-past | `go(…, "turn")`, scroll-past upward `back-turn` |
| 691, 980 | `replaceState`: `revealPage`, `followScroll` | the controller's address write |

### Redraws

Line map: `safely` from the first draw (6125), the store's status (6135), a push
(6150), the store's read (6160) and its failure (6162), the 30-second tick
(6168), a new width (6178), the runs refresh (3767) and the plan follower (5098,
5107, 5128); `draw` directly from `takePlan` (5119). Partial: `drawHappened`
from the depth and filter switches (5679), `drawPlan` from a plan unit button
(5306), the check pager's own `showPage` (2249).

Runs page: `draw()` on load and from `refetch` on every push and runs event
(`runs.js` 203, 232-251), which replaces `#app`'s children with no place, open
state beyond `opened` or focus kept. History page: drawn once (`history.js` 94).

### Open, closed and selected state

| Store | Where | Mechanism | Kept |
| --- | --- | --- | --- |
| `runFoldOpen` | `run-summary.js` 36 | `runFold` (details, eased) | visit |
| deferral rows | `run-summary.js` 634 | disclosure, eased | visit, in `runFoldOpen` |
| `planFolds` | `line-map.js` 4270 | `runFold` with `onChange` | saved (`FOLD_KEY`) |
| `decisionsOpen` | 4721 | native `details` toggle | visit |
| `asideOpen` | 5926 | class toggle on a clamped row, by its words | visit |
| `prev.acrossOpen`, `prev.elsewhereOpen` | 5754, 6038 | native `details` toggle | visit |
| `prev.checkPage` | 2246 | check pager | visit |
| approvals rows (`ap-row`) | 4616 | native `details` | none: lost on every redraw |
| `selected` | 62 | stop and tick handlers | visit; set by hover |
| lit lane | 3248 | class toggle, from hover and focus | none |
| `storyDepthChosen`, `storyFilter` | 64 | the story bar's switches | depth saved (`DEPTH_KEY`), filter visit |
| layout (`PAGED`) | 490 | the chapters tab | saved (`LAYOUT_KEY`) |
| runs page `opened` | `runs.js` 149 | disclosure | visit |
| history rows | `history.js` 70 | disclosure, filled on first open | none (no redraw yet) |

### Focus

`focusMark` (6092) and `refocus` (6104) bring focus back only to an element with
`data-subject`, `data-ticket` or `data-tab`. Depth and filter buttons keep focus
only because their own handler calls them around a partial redraw. Plan rows,
decision summaries, pager buttons, fold summaries, ledger rows and set-aside
rows lose it on a full redraw. The runs page loses focus on every push.

### Hosted patches (`build-assets.mjs`)

Seven anchored patches. Two are page mechanics this plan replaces: the `hostedY`
scroll restore (removed by V26-4003) and the plan chapter `receiveHosted` builds
by hand when a plan first arrives mid-visit, which skips `chapterOf`, so that
chapter is not watched by the `watch` and `arrive` observers and starts already
`arrived` (SLICE-5). A plan seeded before load already goes through `chapterOf`.
The other five are host adapters and stay. Every remaining anchor must still
match exactly once (`hosted.asset_hook_drift`).

## Contracts

### The controller (`page.js`)

`page.js` defines one global, `page`, and nothing else. Its sections are the
five parts below, in order.

- **Location** is `{chapter, item, layout}`: a chapter id, an optional element
  id, and `paged` or `scroll`. `page.here()` returns it. The runs and history
  pages are one-chapter pages whose chapter is their own name.
- **`page.go(location, reason)`** is the only way a page moves. A reason is an
  entry in one table, `page.reasons`, saying how it writes the address (`push`,
  `replace`, `push-if-chapter-changes` or `none`) and how it scrolls (`top`,
  `end`, `item`, `item-or-top`, `keep` or `none`). Built in: `turn` (push, top),
  `back-turn` (push, end), `link` (push-if-chapter-changes, item), `address`
  (replace, item-or-top), `history` (none, item-or-top), `layout` (replace,
  top), `first` (replace, item-or-top), `follow` (replace, none). `go` reads the
  rule from the table and branches on no reason by name; an unknown reason
  throws. In the one-page layout `push` and `push-if-chapter-changes` write as
  `replace` (decision 2), for every reason, declared or built in. It resolves
  item, then chapter, then (from SLICE-3) each fold holding the item, opened;
  writes the address; then scrolls once.
- **`page.start({chapters, show})`** is how a page puts itself under the
  controller: its chapter ids and the function that shows one. Until a page
  calls it, `page.js` binds no handler and routes nothing, so the runs and
  history pages are untouched by it until SLICE-6. The line map calls it from
  SLICE-2; the runs and history pages, as one-chapter pages, from SLICE-6.
- **Scroll by reason** is the only scroll write on a page outside the launcher
  panel. A turn lands instantly; an item lands centred in one move, smooth only
  when `page.motion(true)` allows; a redraw keeps the reader's place by anchor
  (V26-4003); a region registered with `page.sideways(key, node)` keeps its
  distance from its right edge across redraws, as the line does today. A focus
  move that goes with a navigation uses `preventScroll`.
- **`page.redraw(draw, why)`** is V26-4003's transaction, given the page's own
  draw function. SLICE-3 makes it re-apply the UI-state store, SLICE-4 makes it
  restore focus by key.
- **`page.state(key, {saved})`** is one entry in the UI-state store: `get()`,
  `set(value)`. Saved entries use today's storage keys and formats (decision 8).
- **`page.fold(node, {key, saved, oneLine, onSettle})`** is the one fold
  primitive, for a `details` element or a disclosure `{button, region}`
  (decision 12): `runFold`'s motion, opened from the store on every draw,
  recorded at the click. `runFold` in `run-summary.js` becomes a call to it.
- **Focus by key**: every element that takes focus carries `data-key` from
  `page.key(kind, id)`. A redraw puts focus back on the same key in the same
  region; a panel or fold that closes returns focus to the control that opened
  it.
- **`page.motion(fromReader)`** is the one motion check (decision 9).

### Extending the controller

The controller's public calls are the only way a feature reaches the page's
mechanics, and the views' own code uses them as a new feature would. Each slice
adds its rows to an "Extending the page" section of
`skills/delivery-views/SKILL.md`:

| To add | Call | It gets, with no other code |
| --- | --- | --- |
| a kind of navigation | `page.reasons.<name> = {address, scroll}`, then `page.go(location, name)` | its history entry or none (always none in the one-page layout), one scroll by its rule, folds opened to the item |
| a page | `page.start({chapters, show})` | routing, links, Back and Forward, by the same rules |
| a sideways-scrolling region | `page.sideways(key, node)` | its place kept through redraws |
| a redraw trigger | `page.redraw(draw, why)` | the place, focus and open state kept, merged within a frame |
| something open, closed or picked | `page.state(key, {saved})` | kept through redraws; with `saved`, through reloads |
| a fold or disclosure | `page.fold(node, {key})` | the page's motion, its state kept, opened by a deep link to anything inside it |
| a focusable control | `page.key(kind, id)` as its `data-key` | focus returned to it after a redraw |
| motion | `page.motion(fromReader)` | reduced motion and keyboard use respected |

The source pins of SLICE-2 to SLICE-4 hold the other side, and each names the
call to use in its failure message. Each slice ends with a case in
`tests/delivery_views_page/page-controller-extension.test.mjs` that adds its
part from a `run` step, using only `page`, and shows it behaves like the
built-in ones through a tick.

### Goldens

The line-map goldens compare chapter and detail `textContent`; the runs golden
compares the page's text and its two blocks; the history goldens compare the
rendered page's text. None reads attributes or scroll, so `data-key` and the
moved scroll writes leave them byte-identical. No slice recaptures a golden. A
slice that finds one moved has changed what a page says, which is out of scope:
it stops and asks.

## Finish line

1. Scrolled partway down any chapter, in either layout, a 30-second tick, a
   resize, a store push, a status change, a runs refresh and a plan update leave
   the line map's scroll where it was; a push leaves the runs page's scroll
   where it was.
2. In paged layout, each page turn adds one history entry; Back twice and
   Forward once step through the chapters in order, each at its top; the
   controller adds no other entry, and none at all in the one-page layout.
3. A link to `#<id>` inside a closed fold, loaded fresh or followed in the page,
   opens the chapter and every fold holding the target, scrolls once, and the
   folds stay open through the next redraw.
4. Every open, closed or selected state in the inventory survives a full
   redraw; saved state written by today's page is read by the new one, and the
   new one writes the same keys and formats.
5. Hovering a stop or a tick changes neither `selected` nor the panel, before or
   after a push.
6. After a redraw, focus is on the same `data-key` it was on, for every control
   kind in the inventory's focus row, on the line map and the runs page, even
   when a row was inserted above it.
7. No scroll or address write outside `page.js` and the launcher panel, no
   `scroll-behavior: smooth` rule, and one motion check, each held by a source
   pin.
8. The hosted build carries no scroll patch and no hand-built plan chapter; a
   plan arriving mid-visit gets a chapter that is watched and arrives like the
   others.
9. Two ticks with no change in the record replay no entrance animation, while a
   real change still marks its fresh beat; Happened eases its height only when
   the reader changes its depth or filter.
10. Each push or tick draws the line map once, and its runs refresh redraws only
    when one of the seven blocks it reads changed.
11. A navigation reason, a sideways region, a piece of state, a fold, a keyed
    control and a motion check added from outside the controller through its
    public calls each behave like the built-in ones, and `SKILL.md`'s
    "Extending the page" section names each call.
12. Every line-map, runs and history golden is byte-identical.

## Stories

Every slice runs in one lane, in order: each changes `page.js`, `SKILL.md` or the
page lane's shared harness, and each builds on the one before. The delivery starts once V26-3993's V26-3996 has landed on
`main`.

### SLICE-0. Give every delivery view one shared page asset

Lane 1, tier 1, not blocked. Test-first. Files:
`skills/delivery-views/assets/page.js`,
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/run-summary.js`,
`skills/delivery-views/scripts/render.py`,
`hosted/delivery-viewer/scripts/build-assets.mjs`,
`skills/delivery-views/SKILL.md`, `profiles/delivery-views.json`,
`profiles/jira.json`, `profiles/linear.json`, `profiles/run-delivery.json`,
`tests/test_release_integration.py`, `tests/delivery_views_page/` and
`tests/test_delivery_views_render.py`.

- `page.js` with the motion check (decision 9) and `page.key`; `reduced()` and
  `runMotionWanted` become calls to `page.motion`. Nothing else moves yet.
- `render.py` loads `page.js` first on the line map, runs and history pages;
  `build-assets.mjs` prepends it to the renderer's input.
- The new output in the four profiles, the provenance lock (added by hand, then
  the writer), the catalog entry and the release-integration core payload pin.
- The page tests that evaluate `run-summary.js` alone
  (`rich-words.test.mjs` 10-15, `line-map-plan-review.test.mjs` 383-386)
  evaluate `page.js` before it, as the pages do.
- Tests, each shown red on the slice's base:
  - **Loaded first.** Each of the three rendered pages has `page.js`'s banner
    before `run-summary.js`'s; the built hosted renderer starts with it.
    Mutant: the runs page left out.
  - **Keyboard keeps live motion.** After a keydown, a push that lands a story
    still marks the landing tick and flips the digit; under reduced motion it
    doesn't. Mutant: the keyboard clause applied with `fromReader` false.
  - **Reader motion.** After a keydown a fold opens without its height ease;
    after a pointer press it eases again. Mutant: a sticky keyboard flag.
  - **One check.** A pin: among the `.js` assets, `prefers-reduced-motion`
    appears in `page.js` only, in either quote style. The stylesheets' media
    queries are out of the pin. Mutant: a copy in `runs.js`.

### V26-4003. Keep the reader's place when the live view redraws itself

Lane 1, tier 2, after SLICE-0. Test-first. Files:
`skills/delivery-views/assets/page.js`,
`skills/delivery-views/assets/line-map.js`,
`hosted/delivery-viewer/scripts/build-assets.mjs`,
`skills/delivery-views/SKILL.md`, `tests/delivery_views_page/` and
`tests/test_delivery_views_hosted.py`.

- Existing item; its scope, criteria and scenarios stand. The transaction lives
  in `page.js` as `page.redraw`, and the line map's `safely` callers go through
  it.
- The anchor's keys at this slice are the stable ids the page already carries
  (element ids, `data-subject`, `data-ticket`, `data-fold`, chapter ids);
  SLICE-4 widens it to `data-key`.
- `keepPlace` stays (its scope, item 3); SLICE-2 retires it.

### SLICE-2. Route every navigation through one location and scroll each once

Lane 1, tier 3, after V26-4003. Test-first. Files:
`skills/delivery-views/assets/page.js`,
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`,
`skills/delivery-views/SKILL.md` and `tests/delivery_views_page/`.

- The location model, `page.reasons`, `go`, the delegated link handler on
  `document` and the one routing entry for `popstate` and `hashchange`
  (decisions 2 and 3). `route`, `showPage`'s scroll branch, `revealPage` and
  `followScroll` move into `page.js`; every write in the inventory's scroll and
  address table except the launcher list's goes through `go`. The line map
  calls `page.start`. The check pager's local `page` (`line-map.js` 2246) is
  renamed so it no longer shadows the controller.
- `keepPlace` and `prev.lineScroll` retire into the redraw anchor and
  `page.sideways` (decision 5).
- The CSS smooth-scroll rule goes (decision 9).
- `SKILL.md`: the pages paragraph gains Back and Forward; "Extending the page"
  begins with the reason, sideways, redraw and motion rows.
- Tests, each shown red on the slice's base:
  - **Push and replace.** In paged layout, a rail click, a chapter-tab click,
    the corner name, an arrow, a scroll-past and a launcher page each grow
    `history.length` by one. A tick, a push, a layout switch, an item link
    within the chapter shown and, in the one-page layout, scroll-following, a
    chapter-tab click and a What's left row that goes to The line grow it by
    none. In paged layout a What's left row that goes to The line grows it by
    one, and Back from there returns to What's left. An outside `location.hash`
    change grows it by exactly one, the browser's own, and shows the named
    chapter. The hosted shell's skip link to `#app` is not prevented. Mutants:
    `go` always replacing; the tab's links left to the browser;
    `push-if-chapter-changes` always replacing; `go` ignoring the layout; `go`
    pushing for `address`; the handler taking every `#id`.
  - **Back and Forward.** Turn Now, The line, What's left; Back: The line; Back:
    Now; Forward: The line; each at its top, `history.length` unchanged across
    them. Mutant: `go` pushing for `history`.
  - **One move per navigation.** A recording fake counts `scrollTo`,
    `scrollIntoView`, `scroll`, `scrollBy`, a `scrollTop` or `scrollLeft` write
    on the page, and every `focus()` without `preventScroll` during a navigation
    as a move. Exactly one move for each of: a page turn, a What's left row, a
    plan unit, a decision reference, a setback link, Back, and an outside
    `hashchange`. Mutants: `revealPage` scrolling as well; the setback link's
    bare `focus()`; `popstate` and `hashchange` routed apart.
  - **An outside address within the chapter still routes.** With the plan
    chapter shown, an outside `location.hash` change to `#plan-decision-3`
    makes exactly one move, to decision 3, and grows `history.length` by
    exactly one (from SLICE-3 it also opens the decisions fold and decision
    3). Mutant: the routing guard
    comparing the chapter alone.
  - **Focus goes with the link.** A decision reference and a setback link leave
    focus on the decision's summary and on the setback. Mutant: the focus move
    dropped.
  - **Land at end.** Scroll-past upward lands at the previous page's end,
    instantly. Mutant: landing at its top.
  - **Item motion.** An item link scrolls smoothly after a pointer press and
    instantly after a key press or under reduced motion. Mutant: `fromReader`
    false for a link.
  - **Plan of another height.** `line-map-plan.test.mjs`'s within-and-past case
    (line 869) passes with `keepPlace` gone: the guard, unchanged.
  - **Sideways.** The line scrolled 120 from its right edge stays 120 from it
    through a tick and through a push that widens it. Mutant: the absolute
    `scrollLeft` kept.
  - **Reasons are data.** A pin that `go` names no reason literally; an unknown
    reason throws. Mutant: a `reason === "turn"` branch.
  - **Extension: a reason and a region.** From a `run` step, declare
    `page.reasons.peek = {address: "none", scroll: "item"}`, register a made-up
    sideways box, `go` to a stop with `peek` and tick: one move, no history
    entry, the box's place kept. Mutant: `go` treating an unknown name as
    `turn`.
  - **Source pins.** Outside `page.js`, no asset contains `scrollTo`,
    `scrollIntoView`, `scrollBy(`, `.scroll(`, `scrollTop =`, `scrollLeft =`,
    `location.hash =`, `pushState(` or `replaceState(`, except the launcher
    list's one `scrollIntoView`; no `scroll-behavior: smooth` in any stylesheet.
    Each pin is shown red by planting one such write in `line-map.js`, and its
    message names the `page` call to use.
- Harness: the page lane's click step waits for `hashchange` only when the click
  wasn't prevented (`support.mjs` 366-374), so it stops waiting by its own rule
  once the controller prevents same-page links. The layout test's hand-fired
  `hashchange` (`line-map-layout.test.mjs` 82) stays, as an outside address.

### SLICE-3. Keep every open, closed and selected state in one store with one fold

Lane 1, tier 4, after SLICE-2. Test-first. Files:
`skills/delivery-views/assets/page.js`,
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/run-summary.js`,
`skills/delivery-views/SKILL.md` and `tests/delivery_views_page/`.

- `page.state` and `page.fold`; each line-map and run-summary row of the
  inventory's state table moves onto them, saved rows on their current keys
  (decision 8). Native `details` toggles and the aside rows' class toggle become
  `page.fold`; the deferral rows stay disclosures (decision 12).
- Selection: hover names and lights only (decision 6).
- Deep links open their folds (decision 4).
- Tests, each shown red on the slice's base:
  - **Every store survives a redraw.** One case per line-map and run-summary
    row: open or pick it, tick, and it is still open or picked. Red today for
    the approvals row and the lit lane. Mutant: one row's key not registered.
  - **Today's saved state is read, and written back the same.** Seed literal
    `delivery-views:test:layout` = `scroll`, `delivery-views:test:story-depth` =
    `everything` and `delivery-views:test:plan-folds` = `["units"]`: the page
    opens one-page, at that depth, with the units fold open. Change each: the
    stored values are exactly `paged`, `headline` and the new JSON array. No key
    or format is read from the asset. Mutant: a renamed key or a changed format.
  - **Deep link into a fold.** A first load at `#plan-decision-3` opens the
    decisions fold and decision 3, centres it, and keeps both open through a
    tick; the same as an in-page link from a decision reference. Mutant: folds
    opened without the store.
  - **Hover doesn't select.** Hover a stop, then a landed piece's `.mg-tick`:
    after each, before any push, `#detail` shows what was selected before and
    every `aria-pressed` is unchanged; then push, and the same. A click and a
    keyboard focus on each kind still select. Mutants: the tick's `wire`
    keeping `mouseenter`; hover calling `showDetail` without setting
    `selected`. `line-map.test.mjs` 1134 is re-derived to this rule.
  - **One fold.** A pin: outside `page.js`, no `addEventListener("toggle"`,
    `ontoggle`, `.open = ` or `classList` change of `open` on a fold, except the
    theme switch's holder (`line-map.js` 41, out of scope). Mutant: one fold
    left on its native toggle.
  - **Extension: a state and a fold.** From a `run` step, add a `page.fold`
    holding an element with an id, and a `page.state` entry; follow a link to
    that id: the fold opens; tick: both kept. Mutant: `go` opening only folds the
    page drew itself.
  - The run-summary tests (`run-summary-closures`, `run-summary-findings`) pass
    unchanged.
- `SKILL.md`: hovering names and lights, click and focus open the panel; the
  state and fold rows join "Extending the page".

### SLICE-4. Return focus by key after every redraw

Lane 1, tier 5, after SLICE-3. Test-first. Files:
`skills/delivery-views/assets/page.js`,
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/run-summary.js`,
`hosted/delivery-viewer/src/viewer.ts`,
`skills/delivery-views/SKILL.md` and `tests/delivery_views_page/`.

- Every focusable element the line map and run summary build carries a
  `data-key` (decision 7). `focusMark` and `refocus` move into `page.js`, read
  only `data-key`, and run inside `page.redraw`; the depth and filter buttons
  stop calling them by hand. A panel or fold that closes returns focus to its
  opener. The redraw anchor reads `data-key` first.
- `viewer.ts`'s comment naming `data-tab` as the focus mark is updated.
- The page lane's `focused()` helper (`support.mjs` 332) reads `data-key` first,
  then today's three attributes, so existing focus assertions keep their values.
- Tests, each shown red on the slice's base:
  - **Each kind keeps focus.** One case per kind: a depth button, a plan row, a
    decision summary, a pager button, a fold summary, a ledger row, a set-aside
    row, a stop, a ticket, a tab. Focus, tick, same key. Red today for all but
    the last three. Mutant: one kind built without a key.
  - **Same record, not same place.** Focus a plan row and an approvals row, then
    push a record that inserts a sibling before each: focus is on the element
    for the same record id. Mutant: an index passed as the id.
  - **Same key, same region.** The ticket in the detail comes after the one
    under its stop in document order; focus the detail's, tick: the detail's.
    Mutant: first match wins.
  - **Return from a panel.** Open the detail from a stop by keyboard, close it
    with Escape: focus is on the stop. Mutant: focus to `body`.
  - **Every focusable element has a key.** On every line-map fixture page, each
    `a[href], button, summary, input, select, [tabindex]` inside `#app` has a
    non-empty `data-key`. Mutant: the decision summaries without one.
  - **Keys only through `page.key`.** A pin, naming `page.key` in its message.
  - **Extension: a keyed control.** From a `run` step, add a button keyed
    `page.key("probe", "x")`, focus it, tick: focus on it. Mutant: the redraw
    restoring only kinds it knows.
- `SKILL.md`: the focusable-control row joins "Extending the page".

### SLICE-5. Drop the hosted page's own mechanics and the redraws that repeat

Lane 1, tier 6, after SLICE-4. Test-first. Files:
`skills/delivery-views/assets/page.js`,
`skills/delivery-views/assets/line-map.js`,
`hosted/delivery-viewer/scripts/build-assets.mjs`,
`skills/delivery-views/SKILL.md`, `tests/delivery_views_page/`,
`tests/test_delivery_views_hosted.py` and
`tests/test_delivery_views_hosted_projection.py`.

- The hosted plan chapter: `line-map.js` gains a `chapter("plan")` call that
  makes the chapter through `chapterOf` when missing; `receiveHosted`'s
  hand-built section and rail link become that call.
- The runs refresh redraws only on a change (decision 10).
- Happened eases its height only when the reader changes depth or filter.
- A redraw that only keeps the reader's place replays no entrance animation.
- Tests, each shown red on the slice's base:
  - **Hosted plan arriving mid-visit.** A fake store whose first projection has
    no plan and whose second has one: the plan section is observed by both
    observers and starts without `arrived`. Mutant: the hand-built section kept.
  - **No scroll or chapter patch.** A pin in `test_delivery_views_hosted.py`:
    `build-assets.mjs` has no `scrollTo`, no `hostedY` and no
    `el("section", "chapter`; building asserts each remaining anchor matches
    once.
  - **One draw per push, unless a block changed.** A push, then the refresh
    answering with all seven blocks unchanged: one draw. Then one row per block,
    each changing only that block: two draws. Mutant: the comparison reading
    only the runs block.
  - **Happened height.** Two ticks: no `run-height` animation on
    `#happened .inner`; a depth change: one. Mutant: the ease on every draw.
  - **No replayed entrance.** Two ticks with an unchanged record: no element
    gains `fresh`, `arriving`, `landing`, `grew` or `arrive`. Allow side: a push
    adding a beat still marks it `fresh`. Mutant: a beat marked fresh on every
    draw.
- The hosted viewer's browser test that pushes a projection and reads `scrollY`
  and the line's `scrollLeft` (`hosted-viewer.spec.ts` 294) passes unchanged.

### SLICE-6. Bring the runs and history pages onto the controller

Lane 1, tier 7, after SLICE-5. Test-first. Files:
`skills/delivery-views/assets/runs.js`,
`skills/delivery-views/assets/history.js`,
`skills/delivery-views/SKILL.md`,
`tests/delivery_views_page/runs.test.mjs`,
`tests/delivery_views_page/history-page.test.mjs` (new) and
`tests/test_delivery_views_history.py`.

- The runs page calls `page.start` as a one-chapter page and draws through
  `page.redraw`; its run rows are disclosures on `page.fold` keyed
  `run:<runId>`, and every focusable element carries a key. A deep link to
  `#now`, `#ended` or a run opens through the controller's first route. Its
  links to the line map, the history page and the previews stay ordinary links
  that leave the page. Its refetch on a push or runs event keeps place, open
  rows and focus.
- `runs.js` and `history.js` rename their local `page` (`runs.js` 9,
  `history.js` 52) so it no longer shadows the controller.
- The history page's run rows are disclosures on `page.fold` keyed
  `history:<row key>`, each filled on its first open as today, and keyed for
  focus. A deep link to `#<row key>` opens it.
- Neither page reads storage; every row's state is kept for the visit.
- `SKILL.md`: the runs and history paragraphs say they follow the same page
  rules.
- Tests, each shown red on the slice's base:
  - **Runs page keeps place.** Scrolled to an ended run, a push and a runs event
    leave `scrollY` where it was. Mutant: `refetch` drawing directly.
  - **Runs page keeps focus.** Focus an ended run's button, push a record that
    adds a run above it: focus is on the same run. Mutant: keys by position.
  - **Open rows survive.** Open two runs, push: both open; the open panel shows
    the new gate (the existing test at `runs.test.mjs` 106, unchanged).
  - **History deep link.** A first load at a row's id opens that row and
    scrolls it into view once. Mutant: the row left shut.
  - **Links away still leave.** A click on the runs page's line map link is
    not prevented. Mutant: the handler taking a link to another document.
  - **Every focusable element has a key.** SLICE-4's check, over the same
    selector, on the runs page fixture (with previews, open and ended runs) and
    on every history golden case. Mutant: the runs page's links built without
    a key.
  - **Disclosures only through the fold.** A pin: `runs.js` and `history.js`
    set no `hidden` or `aria-expanded` themselves. Mutant: a run row left on its
    own click handler.
  - **No storage.** A pin: neither `runs.js` nor `history.js` names
    `localStorage` or `sessionStorage`, shown red by planting one.
  - **Same pins.** SLICE-2's and SLICE-4's source pins cover `runs.js` and
    `history.js`; shown red by planting a `scrollTo` in `runs.js`.
  - The runs and history goldens pass unchanged.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | SLICE-0, V26-4003, SLICE-2, SLICE-3, SLICE-4, SLICE-5, SLICE-6 | `skills/delivery-views/assets/`, `skills/delivery-views/scripts/render.py`, `skills/delivery-views/SKILL.md`, `hosted/delivery-viewer/scripts/build-assets.mjs`, `hosted/delivery-viewer/src/viewer.ts`, `profiles/delivery-views.json`, `profiles/jira.json`, `profiles/linear.json`, `profiles/run-delivery.json`, `tests/delivery_views_page/`, `tests/test_release_integration.py`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted.py`, `tests/test_delivery_views_hosted_projection.py`, `tests/test_delivery_views_history.py` |

One lane, because every slice changes `page.js` or the page lane's shared
harness, and both lanes would otherwise write `SKILL.md`. SLICE-6 needs only the
calls SLICE-4 completes; it comes after SLICE-5 so no two stories hold the same
files at once. Shared files: `provenance.lock.json` and the `delivery-views`
catalog entry change in every story; a lock-only conflict is resolved by the
provenance writer, as `AGENTS.md` says. No story uses a fixed port or browser
session.

## Test scenarios

1. A reader halfway down The line in paged layout waits a minute: two ticks and
   a runs refresh, and the page has not moved.
2. A reader on Now turns to The line by the rail, then to What's left by the
   chapter tab, then presses Back twice and Forward once: The line, Now, The
   line, each at its top.
3. A reader in the one-page layout scrolls from Now to What's left: the address
   follows, and Back leaves the page rather than stepping through chapters.
4. A reviewer opens a link to the third plan decision: the plan chapter shows,
   the decisions fold and decision 3 open, decision 3 is centred, and a tick
   later all are still open.
5. A reader opens the approvals row for O25, the elsewhere fold and page 2 of
   the checks, and selects PAY-2360, then resizes the window: all four are as
   they left them.
6. A reader moves the pointer across five stops and two ticks: the panel stays
   on the stop they clicked.
7. A keyboard reader tabs to the story bar's "All" filter, then to decision 2's
   summary, and a push arrives at each: focus stays on each; a story landing in
   that push still animates.
8. On the hosted page, a plan published mid-visit gets its chapter, which fades
   in when scrolled to, like the others.
9. A reduced-motion reader follows a setback link: the page moves once,
   instantly, and focus is on the setback.
10. On the runs page, a reader with one run open and focused stays there through
    three pushes.
11. A later feature adds a "peek" navigation and a fold of its own through
    `page` alone, and both behave like the built-in ones.

## Execution posture

`test-first` for every story. Each behaviour above has a clear expected outcome
and a named mutant. Where a test pins today's behaviour that a decision changes
(decision 6's hover test), the story re-derives it and says so.

## Sensors

- Every story: `tests/test_delivery_views_page.py` (the Node page lane,
  including the line-map, runs and history goldens),
  `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.
- SLICE-0: `tests/test_release_integration.py` and
  `tests/test_delivery_views_render.py`.
- SLICE-6: `tests/test_delivery_views_history.py`.
- SLICE-0, V26-4003 and SLICE-5: `tests/test_delivery_views_hosted.py` and
  `tests/test_delivery_views_hosted_projection.py`, plus the hosted viewer's
  own `npm run build` and `npm test` in `hosted/delivery-viewer/`, and
  `npm run test:browser` where Playwright's Chromium is installed (optional;
  reported as not run where it isn't).
- Before any release that carries this: the full suite with
  `--workers 4 --budget tests/suite-budget.json` on Python 3.11, per
  `AGENTS.md`. The added cases are page cases in the existing Node lane, each
  one `openPage`, so the budget holds without raising a ceiling.

## Approval handoffs

1. The operator approves this plan, including decisions 1 to 12.
2. SLICE-0 and SLICE-2 to SLICE-6 are created under V26-4004 with
   `$create-linear-ticket`, each blocked as the stories say; V26-4003 gains
   "blocked by SLICE-0". The epic's description is updated for decision 1 and
   the widened scope.
3. Delivery runs under `orchestrate-work` once V26-3996 has landed. No release is
   cut by this plan.

## Compounding

A solution note: a page that rebuilds itself keeps the reader's place, open
state and focus in one transaction around the draw, keyed by what the record
names, rather than in each caller. A second, if the delivery bears it out:
mechanics a feature must not re-invent are public calls plus pins that name
those calls in their failure message.

## Evidence

- `docs/solutions/test-failures/a-clicked-link-changes-the-page-on-a-later-task-2026-11-24.md`:
  the page lane's click step waits for `hashchange` only when the click wasn't
  prevented, which is why decision 3 needs no harness change for links.
- The epic's inventory of 2026-11-26, re-read against `main` at `a80ceb99` for
  the tables above, with the runs and history pages added.
