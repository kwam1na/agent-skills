---
title: One controlled layer for the live view's navigation and page mechanics - Plan
type: refactor
date: 2026-11-26
topic: live-view-page-controller
reviewSlug: live-view-page-controller
execution: code
status: draft
---

# One controlled layer for the live view's navigation and page mechanics - Plan

## Goal

The live view has one owner for where the reader is, how the page scrolls, what
stays open and where focus goes. A redraw, whatever starts it, leaves the reader
where they were, with the same folds open and the same control focused. A page
turn, a deep link and an in-page link each move the page once, by one rule, and
Back returns to the chapter the reader came from. Hovering never changes what is
selected.

This plan answers epic V26-4004. It changes nothing by itself: slices 2 to 5
below are created as tracked items under V26-4004 once the plan is approved.
Slice 1 is V26-4003, which already exists and keeps its own scope and criteria.

## Scope

- In:
  - one page controller, written as one bounded section of
    `skills/delivery-views/assets/line-map.js`, with the five parts the epic
    names: location and `go(location, reason)`, scroll by reason, the redraw
    transaction, one keyed UI-state store with one fold primitive, and focus by
    key;
  - one motion check shared by `line-map.js` and `run-summary.js`;
  - the folds `run-summary.js` draws (`runFold`, its deferral rows), which the
    fold primitive takes over;
  - the hosted viewer's anchored patches in
    `hosted/delivery-viewer/scripts/build-assets.mjs` that keep scroll or build
    the plan chapter by hand;
  - the bugs the inventory found: hover changes the persistent selection, deep
    links don't open the folds holding their target, the hosted plan chapter
    skips the page's observers, Happened re-eases its height on every redraw, and
    the local page draws twice for each push or tick;
  - the page rules in `skills/delivery-views/SKILL.md` those changes alter.
- Out:
  - what any chapter says or draws: words, marks, order and layout stay as they
    are, so the text goldens hold (see "Goldens");
  - the theme switch and its stored pick (`line-map.js` lines 1 to 40), which is
    already one store with one key;
  - the launcher's own list scroll (`row.scrollIntoView({block: "nearest"})`
    inside the launcher panel), a scroll inside a fixed overlay, not the page;
  - a new asset file, and any change to `render.py`'s asset wiring or the hosted
    build's file list (decision 1);
  - the hosted viewer's own shell, `viewer.ts`, beyond what a removed patch
    needs;
  - the shared-seat words V26-3993 is changing.

## Decisions recorded here

The operator aligned on the design on 2026-11-26 (the epic's description). These
are the calls the plan adds; the operator confirms or overturns them in
approving it.

1. **The controller is a bounded section of `line-map.js`.** It sits between the
   page shell (chapters, rail, tab) and the first chapter drawer, under one
   banner comment, and exposes one object, `page`, to the rest of the file. No
   new asset, so `render.py` and `build-assets.mjs` load nothing new. (Epic
   decision, restated so the stories can cite it.)
2. **Page turns push; everything else replaces.** A turn to another chapter in
   paged layout (rail, tab, arrows, scroll-past, launcher, an in-page link to
   another chapter) adds one history entry. Following the scroll in the
   one-page layout, a redraw, the first route, and an item link within the
   chapter already shown use `replaceState`. Back and Forward fire `popstate`,
   and the controller routes from it as from a first load, with the reason
   `history`. (Epic decision.)
3. **The controller handles in-page link clicks itself.** One delegated click
   handler on `#app` and the rail takes any same-page `#id` link, cancels the
   browser's own jump and calls `go`. So `location.hash = …` is written nowhere
   outside the controller, and the page hears no `hashchange` of its own
   making. A `hashchange` it did not make (a typed address, an outside link) is
   still routed, with the reason `address`.
4. **Deep links open their folds in slice 3, not slice 2.** The epic puts
   "deep links that open their folds" in slice 2. Opening a fold so that a redraw
   keeps it open needs the one fold primitive, which arrives in slice 3. Done in
   slice 2, it would need a special case for each of the three mechanisms, which
   slice 3 then deletes. Slice 2 resolves the item and chapter and scrolls once;
   slice 3 adds "and each fold holding it, opened". The epic's order of slices
   is otherwise kept.
5. **`keepPlace` and `prev.lineScroll` go in slice 2.** V26-4003 leaves the plan
   chapter's `keepPlace` in place (its scope, item 3). Slice 2 retires it and the
   line's sideways keeper, because "only the controller writes the scroll" is
   slice 2's rule. The anchor V26-4003 adds covers `keepPlace`'s case: an anchor
   below the plan keeps its offset when the plan above it changes height, and an
   anchor inside the plan keeps its own. V26-4003 removes the hosted `hostedY`
   patch (its scope, item 2), so of the three keepers only two remain for slice 2.
6. **Hover names and lights; it never selects.** Hovering a stop or a tick names
   it and lights its lane, as today. It no longer opens the detail panel or
   changes `selected`. Click, Enter, Space and keyboard focus select. A reader
   pointing across the line therefore no longer swaps the panel under them. The
   test that pins today's behaviour ("keeps a stop chosen by pointing at it
   through an update") is re-derived to the new rule, not deleted.
7. **Keys come from what the record already names.** A `data-key` is built from
   the kind of control and the record's own id for it (`stop:PAY-2358`,
   `fold:plan:units`, `decision:3`, `pager:checks`, `depth:all`), never from its
   position on the page or from record text that isn't an id. Two controls with
   the same key are allowed only when they stand for the same thing in two
   places (a ticket under its stop and in the detail), and focus then returns to
   the one in the same region, as `refocus` does today.
8. **Visit-kept and saved state stay exactly where they live today.** Saved
   state keeps its storage keys and formats (`LAYOUT_KEY`, `FOLD_KEY`,
   `DEPTH_KEY`), so a reader's remembered layout, plan folds and depth survive
   the upgrade. Everything else stays in memory for the visit, as today.
9. **One motion check, two inputs.** Motion runs only when the reader has not
   asked for reduced motion and the last input was not the keyboard: the rule
   `runMotionWanted` already applies to folds. `reduced()` in `line-map.js`, the
   CSS `html { scroll-behavior: smooth }` rule and each `behavior:` choice read
   that one check instead. The CSS rule goes, so a scroll moves smoothly only
   when the controller asks for it.
10. **The 30-second tick and the runs refresh draw once.** A push or tick draws at
    once, as today; the runs refresh three seconds later redraws only when what
    it read differs from what is in use. Redraws asked for within one animation
    frame merge into one (V26-4003).

## Inventory

As read on `main` at `a80ceb99`. Line numbers will move when V26-3996 lands;
each story re-reads its sites at its own base before changing them.

### Scroll writes (`line-map.js`)

| Line | Site | Becomes |
| --- | --- | --- |
| 617 | layout switch, one-page: section `scrollIntoView` | `go({chapter}, "layout")` |
| 677, 679, 681, 683 | `showPage`: target centred, land at end, top, one-page target | the controller's turn and item rules |
| 871 | launcher page command, one-page: section `scrollIntoView` | `go({chapter}, "launcher")` |
| 3383 | line's sideways `scrollLeft` from `prev.lineScroll` | a sideways region registered by key |
| 4234 | What's left row: reveal, then smooth section scroll | `go({chapter: "line", item}, "link")` |
| 4745 | `openDecision`: smooth centre | `go({chapter: "plan", item}, "link")` |
| 5143 | `keepPlace` | the redraw anchor |
| 5316 | plan unit button: reveal, centre | `go({chapter: "plan", item}, "link")` |
| 5665 | setback link: focus, reveal, nearest | `go({chapter: "happened", item}, "link")` |
| 796 | launcher list row, nearest | unchanged (inside the launcher) |

Plus the hash writes at 649 (arrows), 869 (launcher), 936 (scroll-past), the
`replaceState` calls at 691 (`revealPage`) and 980 (`followScroll`), and the
hosted `hostedY` patch, which V26-4003 removes.

### Redraws

`safely` is called from the first draw (6125), the store's status (6135), a
push (6150), the store's read (6160) and its failure (6162), the 30-second tick
(6168), a new width (6178), the runs refresh (3767) and the plan follower (5098,
5107, 5128). `draw` is also called directly by `takePlan` (5119). Partial
redraws: `drawHappened` from the depth and filter switches (5679), `drawPlan`
from a plan unit button (5306), and the check pager's own `showPage` (2249).

### Open, closed and selected state

| Store | Where | Mechanism | Kept |
| --- | --- | --- | --- |
| `runFoldOpen` | `run-summary.js` 36 | `runFold` (details, eased) | visit |
| deferral rows | `run-summary.js` 634 | button and `hidden`, eased | visit, in `runFoldOpen` |
| `planFolds` | 4270 | `runFold` with `onChange` | saved (`FOLD_KEY`) |
| `decisionsOpen` | 4721 | native `details` toggle | visit |
| `asideOpen` | 5926 | class toggle on a clamped row, by its words | visit |
| `prev.acrossOpen`, `prev.elsewhereOpen` | 5754, 6038 | native `details` toggle | visit |
| `prev.checkPage` | 2246 | check pager | visit |
| approvals rows (`ap-row`) | 4616 | native `details` | none: lost on every redraw |
| `selected` | 62 | stop and tick handlers | visit; set by hover |
| lit lane | 3248 | class toggle, from hover and focus | none |
| `storyDepthChosen`, `storyFilter` | 64 | the story bar's switches | depth saved (`DEPTH_KEY`), filter visit |
| layout (`PAGED`) | 490 | the chapters tab | saved (`LAYOUT_KEY`) |

### Focus

`focusMark` (6092) and `refocus` (6104) bring focus back only to an element with
`data-subject`, `data-ticket` or `data-tab`. Depth and filter buttons keep focus
only because their own handler calls them around a partial redraw. Plan rows,
decision summaries, pager buttons, fold summaries and ledger rows lose it on a
full redraw.

### Hosted patches (`build-assets.mjs`)

Seven anchored patches. Two are page mechanics this plan replaces: the
`hostedY` scroll restore around `safely(doc)` (removed by V26-4003) and the
plan chapter `receiveHosted` builds by hand, which skips `chapterOf`, so the
chapter is not watched by the `watch` and `arrive` observers and starts already
`arrived` (slice 5). The other five (live line, runs refresh, `servedLive`, the
projection on subscribe, `receiveHosted`'s data) are host adapters and stay.
Every remaining anchor must still match exactly once
(`hosted.asset_hook_drift`).

## Contracts

### The controller

- **Location** is `{chapter, item, layout}`: a chapter id, an optional element
  id inside it, and `paged` or `scroll`. `page.here()` returns it.
- **`page.go(location, reason)`** is the only way the page moves. Reasons:
  `turn` (rail, tab, arrows, scroll-past, launcher), `link` (an in-page link or
  a control that points at an item), `address` (a typed or outside hash),
  `history` (Back, Forward), `layout` (the layout switch), `first` (the first
  route), `follow` (the one-page layout's scroll-following, which writes the
  address only). It resolves item, then chapter, then (from slice 3) each fold
  holding the item, opened; then writes the address (push or replace, decision
  2); then scrolls once.
- **Scroll by reason**, the only scroll writes on the page outside the launcher
  panel:
  - a turn lands at the top, or at the end when it came from the next page
    (scroll-past upward), instantly;
  - an item lands centred, in one move, smooth only when the motion check
    allows;
  - a redraw keeps the reader's place by anchor (V26-4003);
  - a sideways region registers with `page.sideways(key, node)` and keeps its
    distance from its right edge across redraws, as the line does today.
- **`page.redraw(doc, why)`** is V26-4003's transaction. Slice 4 makes it also
  capture and restore focus by key, and slice 3 makes it re-apply the UI-state
  store.
- **`page.state(key, {saved})`** returns one entry in the UI-state store:
  `get()`, `set(value)`. Saved entries map onto today's storage keys (decision
  8).
- **`page.fold(details, {key, saved, oneLine, onSettle})`** is the one fold
  primitive: `runFold`'s motion, opened from the store on every draw, recorded
  at the click. Native `details` toggles and class toggles are converted to it.
  `runFold` stays as its name in `run-summary.js`, which the hosted build loads
  first, and reads and writes the same store.
- **Focus by key**: every element that takes focus carries `data-key`. A redraw
  puts focus back on the same key in the same region; a panel or fold that
  closes returns focus to the control that opened it.

### Goldens

The line-map goldens (`tests/fixtures/delivery-views/line-map/*/golden.json`)
compare chapter and detail `textContent`, never attributes or scroll, so adding
`data-key` and moving scroll writes leaves them byte-identical. No slice
recaptures a golden. A slice that finds one moved has changed what a chapter
says, which is out of scope: it stops and asks rather than recapturing.

## Finish line

1. Scrolled partway down any chapter, in either layout, a 30-second tick, a
   resize, a store push, a status change, a runs refresh and a plan update leave
   the scroll where it was (V26-4003, then slice 2 for the plan and the line's
   sideways scroll).
2. In paged layout, each page turn adds one history entry; Back returns to the
   previous chapter at its top; scroll-following and redraws add none.
3. A link to `#<id>` inside a closed fold, loaded fresh or followed in the page,
   opens the chapter and every fold holding the target, scrolls once, and the
   folds stay open through the next redraw.
4. Every open, closed or selected state in the inventory survives a full
   redraw, and the saved ones survive a reload under their current storage keys.
5. Hovering a stop or a tick changes neither `selected` nor the panel.
6. After a full redraw, focus is on the same `data-key` it was on, for every
   control kind in the inventory's focus row.
7. No scroll write outside the controller and the launcher panel, no
   `location.hash =` outside the controller, no `scroll-behavior: smooth` rule,
   and one motion check, each held by a source pin.
8. The hosted build carries no scroll patch and no hand-built plan chapter; the
   hosted plan chapter is watched and arrives like the others.
9. Two ticks with no change in the record replay no entrance animation, and
   Happened eases its height only when the reader changes its depth or filter.
10. Each push or tick draws the page once, and the runs refresh redraws only
    when what it read changed.
11. Every unshared and shared line-map golden is byte-identical.

## Stories

All five slices change `line-map.js`, so they run in one lane, one after
another. The delivery starts once V26-3993's V26-3996 has landed on `main`.

### V26-4003. Keep the reader's place when the live view redraws itself

Lane 1, tier 1, not blocked. Test-first. Files:
`skills/delivery-views/assets/line-map.js`,
`hosted/delivery-viewer/scripts/build-assets.mjs`,
`skills/delivery-views/SKILL.md`, `tests/delivery_views_page/` and
`tests/test_delivery_views_hosted.py`.

- Existing item; its scope, criteria and scenarios stand as written.
- The anchor's keys at this slice are the stable ids the page already carries
  (element ids, `data-subject`, `data-ticket`, `data-fold`, chapter ids). Slice
  4 widens it to `data-key`.
- `keepPlace` stays (its scope, item 3); slice 2 retires it.

### SLICE-2. Route every navigation through one location and scroll each once

Lane 1, tier 2, after V26-4003. Test-first. Files:
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`,
`skills/delivery-views/assets/run-summary.js`,
`skills/delivery-views/SKILL.md` and `tests/delivery_views_page/`.

- The location model, `go(location, reason)`, the delegated link handler and
  `popstate` routing (decisions 2 and 3). `route`, `showPage`'s scroll branch,
  `revealPage` and `followScroll` become the controller's; every scroll write
  in the inventory except the launcher list's goes through it.
- `keepPlace` and `prev.lineScroll` retire into the redraw anchor and
  `page.sideways` (decision 5).
- The single motion check (decision 9): `reduced()` and `runMotionWanted` become
  one function, and the CSS `scroll-behavior: smooth` rule is removed with its
  reduced-motion override.
- `SKILL.md`: the pages paragraph gains Back and Forward; the plan chapter's
  "redraws with the reader's open folds and scroll position kept" stays true.
- Tests, each shown red on the slice's base:
  - **Push and replace.** In paged layout: a rail click, an arrow, a scroll-past
    and a launcher page each grow `history.length` by one; a tick, a push and
    one-page scroll-following grow it by none. Mutant: `go` always replacing.
  - **Back.** Turn from Now to The line to What's left, then `history.back()`:
    The line is shown, at its top. Mutant: `popstate` not routed.
  - **One scroll per navigation.** A recording `scrollTo`/`scrollIntoView` fake
    sees exactly one call for each of: a page turn, a What's left row, a plan
    unit, a decision reference, a setback link. Today's What's left row makes
    two (reveal, then smooth). Mutant: `revealPage` scrolling as well.
  - **Land at end.** Scroll-past upward lands at the previous page's end,
    instantly. Mutant: landing at its top.
  - **Item motion.** An item link scrolls smoothly with motion allowed and
    instantly after a key press or under reduced motion. Mutant: the keyboard
    input ignored.
  - **Plan of another height.** `line-map-plan.test.mjs`'s within-and-past case
    (line 869) passes with `keepPlace` gone. It is the guard, unchanged.
  - **Sideways.** The line scrolled left by 120 from its right edge stays there
    through a tick. Mutant: the sideways region not registered.
  - **Source pins.** No `scrollTo`, `scrollIntoView`, `scrollLeft =` or
    `location.hash =` outside the controller's banner-bounded section, except
    the launcher list's one `scrollIntoView`; no `scroll-behavior: smooth` in
    `line-map.css`; `matchMedia("(prefers-reduced-motion` appears once across
    the two assets.
- Harness: the page lane's click step (`support.mjs` around line 360) already
  waits for `hashchange` only when the click was not prevented. The controller
  prevents same-page link clicks, so the step stops waiting by its own rule. Its
  `navigations` record and the `layout` test's hand-fired `hashchange` (line 82)
  stay, as an outside address change.

### SLICE-3. Keep every open, closed and selected state in one store with one fold

Lane 1, tier 3, after SLICE-2. Test-first. Files:
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/run-summary.js`,
`skills/delivery-views/SKILL.md` and `tests/delivery_views_page/`.

- `page.state` and `page.fold`; each row of the inventory's state table moves
  onto it, with saved rows on their current storage keys (decision 8).
- Native `details` toggles (`decisionsOpen`, across, elsewhere, approvals rows)
  and the class-toggled aside rows and deferral rows become `page.fold`.
- Selection: hover names and lights only (decision 6).
- Deep links open their folds (decision 4): `go` opens each fold holding its
  item, through the store, before it scrolls.
- Tests, each shown red on the slice's base:
  - **Every store survives a redraw.** One case per inventory row: open or pick
    it, tick, and assert it is still open or picked. Red today for the approvals
    row and the lit lane. Mutant: one row's key not registered.
  - **Saved state survives a reload.** Plan folds, depth and layout set in one
    page, read by a second page given the first's storage. Also: a storage value
    written by today's page (`FOLD_KEY`'s array of names) is read by the new one.
    Mutant: a renamed key.
  - **Deep link into a fold.** A first load at `#plan-decision-3` and an in-page
    link to a check inside a closed checks fold each open the fold, show the
    target and keep it open through a tick. Mutant: folds opened without the
    store.
  - **Hover doesn't select.** Hover a stop, then push: the panel shows what was
    selected before the hover. Click it, then push: it stays selected. The test
    at `line-map.test.mjs` 1134 is re-derived to this rule. Mutant: `mouseenter`
    still calling `show`.
  - **One fold.** A source pin: no `addEventListener("toggle"` and no
    `classList.toggle("open"` for a fold in either asset; every `details` the
    page builds passes through `page.fold` or `runFold`. Mutant: one fold left
    on its native toggle.
  - The run-summary fold tests (`run-summary-closures`, `run-summary-findings`)
    pass unchanged.
- `SKILL.md`: the naming-and-lighting paragraph says hovering names and lights,
  and that click and focus open the panel.

### SLICE-4. Return focus by key after every redraw

Lane 1, tier 4, after SLICE-3. Test-first. Files:
`skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/run-summary.js`,
`hosted/delivery-viewer/src/viewer.ts`,
`skills/delivery-views/SKILL.md` and `tests/delivery_views_page/`.

- Every focusable element the page builds carries a `data-key` (decision 7).
  `focusMark` and `refocus` read only `data-key`, and the redraw transaction
  calls them. The depth and filter buttons stop calling them by hand.
- A panel or fold that closes returns focus to the control that opened it.
- The redraw anchor (V26-4003) reads `data-key` first, then its slice-1 ids.
- `viewer.ts`'s comment that `data-tab` is the renderer's focus mark is updated;
  the hosted live line keeps a key.
- The page lane's `focused()` helper (`support.mjs` around line 330) reads
  `data-key` first, then today's three attributes, so existing focus assertions
  keep their values.
- Tests, each shown red on the slice's base:
  - **Each kind keeps focus.** One case per control kind: a depth button, a plan
    row, a decision summary, a pager button, a fold summary, a ledger row, a
    stop, a ticket, a tab. Focus it, tick, and `focused` is the same key. Red
    today for all but the last three. Mutant: one kind built without a key.
  - **Same key, same region.** A ticket under its stop and in the detail: focus
    the detail's, tick, and focus is on the detail's. Mutant: first match wins.
  - **Return from a fold or panel.** Open the detail panel from a stop by
    keyboard, close it with Escape: focus is on the stop. Mutant: focus to
    `body`.
  - **Every focusable element has a key.** On every line-map fixture page, each
    element matching `a[href], button, summary, [tabindex]` inside `#app` has a
    non-empty `data-key`. Mutant: the decision summaries without one.
  - **Keys from ids.** No key contains a number that only counts position: a
    source pin that `data-key` is set only through the controller's `key(kind,
    id)` helper.

### SLICE-5. Drop the hosted page's own mechanics and the redraws that repeat

Lane 1, tier 5, after SLICE-4. Test-first. Files:
`skills/delivery-views/assets/line-map.js`,
`hosted/delivery-viewer/scripts/build-assets.mjs`,
`skills/delivery-views/SKILL.md`, `tests/delivery_views_page/`,
`tests/test_delivery_views_hosted.py` and
`tests/test_delivery_views_hosted_projection.py`.

- The hosted plan chapter: `line-map.js` gains `page.chapter("plan")`, which
  makes the chapter through `chapterOf` when it is missing. `receiveHosted`'s
  hand-built section and rail link are replaced by that one call, so the
  chapter is watched and arrives like the others.
- The local double redraw (decision 10): the runs refresh compares what it read
  with what is in use and redraws only on a difference.
- Happened eases its height only when the reader changes depth or filter, not
  on every `drawHappened`.
- A redraw that only keeps the reader's place replays no entrance animation.
- Tests, each shown red on the slice's base:
  - **Hosted plan chapter.** With a fake store whose `projection()` carries a
    plan, the built renderer's plan section is observed by both observers and
    starts without `arrived`. Mutant: the hand-built section kept.
  - **No scroll or chapter patch.** A pin in `test_delivery_views_hosted.py`:
    `build-assets.mjs` has no `scrollTo`, no `hostedY` and no
    `el("section", "chapter`. Each remaining anchor matches exactly once in
    `line-map.js` (the build's own guard, asserted by building).
  - **One draw per push.** A push, then the runs refresh answering with the same
    runs, links, reviews, needs, files, left-open and integration blocks: one
    draw. The same refresh with a changed runs block: two. Mutant: the refresh
    redrawing unconditionally.
  - **Happened height.** Two ticks: no `run-height` animation on
    `#happened .inner`. A depth change: one. Mutant: the ease on every draw.
  - **No replayed entrance.** Two ticks with an unchanged record: no element
    gains `fresh`, `arriving`, `landing`, `grew` or `arrive`. Mutant: a beat
    marked fresh on each draw.
- The hosted viewer's browser test that pushes a projection and reads `scrollY`
  and the line's `scrollLeft` (`hosted-viewer.spec.ts` around line 294) passes
  unchanged; it is the real-browser guard for the removed patches.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-4003, SLICE-2, SLICE-3, SLICE-4, SLICE-5 | `skills/delivery-views/assets/`, `skills/delivery-views/SKILL.md`, `hosted/delivery-viewer/scripts/build-assets.mjs`, `hosted/delivery-viewer/src/viewer.ts`, `tests/delivery_views_page/`, `tests/test_delivery_views_hosted.py`, `tests/test_delivery_views_hosted_projection.py` |

One lane, because every slice rewrites the same section of `line-map.js` and
each builds on the one before. Shared files: `provenance.lock.json` and the
`delivery-views` catalog entry change in every story; the merge queue
serializes them, and a lock-only conflict is resolved by the provenance writer,
as `AGENTS.md` says. No story uses a fixed port or browser session: the page
lane runs under Node from the checkout's own `node_modules/`, and the hosted
browser test is run by whoever runs the hosted viewer's own suite.

## Test scenarios

1. A reader halfway down The line in paged layout waits a minute: two ticks and
   a runs refresh, and the page has not moved (V26-4003).
2. A reader on Now clicks The line in the rail, then What's left, then presses
   Back twice: What's left, The line, Now, each at its top.
3. A reader in the one-page layout scrolls from Now to What's left: the address
   follows, and Back leaves the page rather than stepping through chapters.
4. A reviewer opens a link to the third plan decision: the plan chapter shows,
   the decisions fold and decision 3 open, decision 3 is centred, and a tick
   later all are still open.
5. A reader opens the approvals row for O25, the elsewhere fold and page 2 of
   the checks, and selects PAY-2360, then resizes the window: all four are as
   they left them.
6. A reader moves the pointer across five stops: the panel stays on the stop
   they clicked.
7. A keyboard reader tabs to the depth switch's "All", then to decision 2's
   summary, and a push arrives at each: focus stays on each.
8. A reader opens the hosted page of a delivery with a plan: the plan chapter
   fades in when scrolled to, like the others, and the rail marks it current.
9. A reduced-motion reader follows a setback link: the page moves once,
   instantly.

## Execution posture

`test-first` for every story. Each behaviour above has a clear expected outcome
and a named mutant. Where a test pins today's behaviour that a decision changes
(decision 6's hover test), the story re-derives it and says so.

## Sensors

- Every story: `tests/test_delivery_views_page.py` (the Node page lane,
  including `golden.test.mjs`'s byte-for-byte check),
  `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.
- V26-4003 and SLICE-5: `tests/test_delivery_views_hosted.py` and
  `tests/test_delivery_views_hosted_projection.py`, plus the hosted viewer's
  own `npm run build` and `npm test` in `hosted/delivery-viewer/`, and
  `npm run test:browser` where Playwright's Chromium is installed (optional;
  reported as not run where it isn't).
- Before any release that carries this: the full suite with
  `--workers 4 --budget tests/suite-budget.json` on Python 3.11, per
  `AGENTS.md`. The added cases are page cases in the existing Node lane, each
  one `openPage`, so the budget holds without raising a ceiling.

## Approval handoffs

1. The operator approves this plan, including decisions 1 to 10.
2. SLICE-2 to SLICE-5 are created under V26-4004 with `$create-linear-ticket`,
   each blocked by the one before, SLICE-2 by V26-4003.
3. Delivery runs under `orchestrate-work` once V26-3996 has landed. No release is
   cut by this plan.

## Compounding

A solution note: a page that rebuilds itself keeps the reader's place, open
folds and focus in one transaction around the draw, keyed by what the record
names, rather than in each caller. V26-4003 already names the first half.

## Evidence

- `docs/solutions/test-failures/a-clicked-link-changes-the-page-on-a-later-task-2026-11-24.md`:
  the page lane's click step waits for `hashchange` only when the click wasn't
  prevented, which is why decision 3 needs no harness change for links.
- The epic's inventory of 2026-11-26, re-read against `main` at `a80ceb99` for
  the tables above. Its "eight stores" are the state table's rows other than
  layout, depth and lit lane, counted by mechanism; the table lists every row
  this plan migrates.
