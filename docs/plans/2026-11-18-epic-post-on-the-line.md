---
reviewSlug: epic-post-on-the-line
---

# Draw the epic's post on the line, with sibling repositories turning in

A delivery with siblings shows them only in its "Across repositories" chapter. The line still ends at this
repository's finish line, so it never shows how far the whole epic has come. This plan keeps that post and runs the
main line on past it to a second post for the epic. Sibling repositories turn in at the epic's post, from rows under
this repository's lanes. The operator agreed the design on 2026-11-18 from a mockup built on the page's own stylesheet
and merge geometry. The mockup is attached to V26-3630 as `epic-post-mockup.html`, with five states: waiting,
unreadable, many siblings, met and other epic.

Checked against `main` at `6ff36e8a` on 2026-11-18:

- The line is `drawLine` and `mergeDrawing` in `skills/delivery-views/assets/line-map.js`. Its geometry is fixed:
  `LINE_STOP_Y = 23`, `LINE_PITCH = 84`, `MERGE_WIDTH = 176`, with the join at x 100 and the post at x 162.
  `drawFlow` uses the same constants for the motion. The stylesheet matches the width in two places:
  `.lines-track`'s right padding of 176px and its least width (`420px`, and `292px` in the phone layout, where the
  name column is 104px).
- The page already gets each sibling worded once, as `doc.across.groups` from `with_across` in
  `skills/orchestrate-work/scripts/render_state.py`. Each group has `name`, `link`, `fresh`, `lastOutcome`, `readAt`,
  `since`, `reason`, and `stories` with `landed`. `drawAcross` draws the chapter from it.
- The state tool decides "another epic". When a read's `trackerParent` isn't the record's `meta.trackerParent`, the
  tool sets `lastOutcome` to `other-epic`, and `across` gives that group no stories. The view never compares parents
  itself.
- A story of this repository that waits on a sibling's story has a wait in `doc.waits` with `cause: "sibling"` and
  `on` set to the sibling story's key.
- `tests/delivery_views_page/line-map-siblings.test.mjs` covers the chapter. The goldens under
  `tests/fixtures/delivery-views/line-map/` have no siblings.
- In jsdom, `track.clientWidth` is 0, so `drawFlow` returns before it draws. `openPage`'s `motion` scenario stubs
  heights and `animate`, but not widths or boxes.
- The SVG sits 16px down a track whose top padding is 16px, and the `.lines` box clips anything above the track. Today
  the "Finish line" label already uses that headroom. With one lane, the post's top is 3px below the SVG's top.
- Two texts describe the sibling behaviour: the delivery-views entry in `catalog.json`, and
  `skills/delivery-views/SKILL.md` under "What the views show of siblings".

## Finish line

- Both plan lenses align.
- V26-3630 has landed on `main` through the merge queue, and every acceptance criterion on the ticket holds.
- A record with no same-epic sibling draws the line exactly as before. Every golden is unchanged, and the drawing is
  still 176px wide.
- The delivery-views entry in `catalog.json` and "What the views show of siblings" in `skills/delivery-views/SKILL.md`
  describe the epic's post, the sibling rows, the chip and the "Also linked" row. Provenance is refreshed in the same
  change.
- The full local suite passed on macOS with Python 3.11 at the base head after V26-3630 landed, run with
  `--workers 4 --budget tests/suite-budget.json`. It stayed inside the budget.
- This delivery doesn't tag a release. The change is additive, so a minor version.

## Decisions

1. **What is drawn.** The line reads only `doc.across.groups`. A group is a same-epic sibling unless its
   `lastOutcome` is `other-epic`. That is the record's own comparison of `trackerParent`, and it stands for the
   ticket's "matches this delivery's". Every rule below (the ranking, unreadable, the counts and the `aria-label`)
   reads same-epic groups only. A group under another epic draws nothing on the line and counts for nothing there. The
   chapter names it under an "Also linked" row, after the drawn groups, with its reason. With no same-epic sibling,
   `mergeDrawing` takes today's path unchanged.
2. **Which two are drawn** (operator, 2026-11-18). There are at most two sibling rows. A sibling is "waited on" when
   one of its story keys is the `on` of a `cause: "sibling"` wait in `doc.waits`. Waited-on siblings come first, in
   the record's order. The rest follow, furthest behind first: the lowest share of landed stories, then the most open
   stories, then the record's order. A fresh sibling with no stories has nothing to be behind on, so it ranks last.
   An unreadable sibling with no stories ranks as share 0. The first two in that order are drawn. The rest fold into a
   "+N more" chip by the epic's post, whose accessible name lists the folded siblings. The order is worked out again
   on every draw, from the document being drawn.
3. **Two posts** (operator, 2026-11-18). This repository's "Finish line" post keeps its place, label and counts. The
   trunk and both posts stay at today's `trunkY`, centred on this repository's lanes alone. Sibling rows add height
   below and don't move the trunk. Past this repository's post, the main line runs on to the epic's post. The epic's
   label, its count and any unreadable sibling's words stack above that post, because sibling turns come in from
   below.
4. **One width, and room above.** With siblings drawn, the drawing widens from 176px to 304px. `mergeDrawing` sets
   `--merge-width` on the track (176px, or 304px with siblings) and `drawFlow` reads its x positions from the same
   number. `.lines-track`'s right padding and both least widths, wide and phone, are written from `var(--merge-width)`.
   No literal `176px`, `420px` or `292px` is left in those rules: `420px` becomes 244px plus the width, and `292px`
   becomes 116px plus the width. The phone layout keeps its 104px name column. With siblings drawn, the drawing also
   sets `--merge-top`. That is the track's top padding and the SVG's top offset, 16px without siblings, raised by the
   height of the epic's stacked text so that every line of it lies inside the track. The no-sibling path keeps 16px,
   and the `.lines` box's negative margin follows the same property.
5. **The epic's count.** It counts this repository's pieces plus every same-epic sibling's stories from its last kept
   read, drawn or folded, as "L/T landed". The epic's line and post are solid only when all three hold: every one of
   this repository's pieces has landed, every same-epic sibling story has landed, and every same-epic sibling is
   fresh.
6. **Sibling rows.** A row goes under this repository's lanes, on the same 84px pitch, with its stop 23px down. It
   shows the sibling's name and "L of T landed", and has no stop buttons; the chapter holds its stories. The row turns
   in at the epic's post from below, in `--edge`. A fresh sibling's turn is solid once it has stories and all of them
   have landed. Otherwise it is dashed, including a fresh sibling with no stories.
7. **An unreadable sibling** (operator, 2026-11-18). A same-epic group whose `fresh` is not `true` is unreadable:
   stale, unavailable, incompatible or never read. Its turn is dotted, with a dash pattern that differs from the
   dashed turn's. Its row reads "Never read" when its `readAt` is null, whatever its `since`. Otherwise it reads
   "Couldn't be read since <time>", with the group's `since`. The epic's line and post go dashed in `--edge`, and the
   label reads "Epic ?". Under the label goes the unreadable sibling's name when only one is unreadable, or "N can't
   be read" when more are. Every new stroke is `--edge`. No `--held` or `--wait` colour is used, and no colour token is
   added.
8. **What the drawing says.** Its `aria-label` keeps today's sentence, then adds the epic's count across its
   repositories, for example "the epic: 5 of 9 pieces landed across 3 repositories". It names each unreadable sibling,
   and the number folded into the chip. The epic's count goes through `figureText` as `merge-epic`, like this
   repository's, so a changed count flips the same way.
9. **The motion.** `drawFlow` stops the trunk stream at this repository's post, as it does today. Nothing flows
   toward the epic's post, because no check of this repository runs there.
10. **The docs.** The delivery-views entry in `catalog.json` and "What the views show of siblings" in
    `skills/delivery-views/SKILL.md` say what the line now draws. That covers the epic's post and its count, at most
    two sibling rows and the chip, the unreadable state, and another epic's group named under "Also linked" and not
    drawn.

## Out of scope

- The hosted page, which stays with V26-3605. The public projection drops `across` and `state.siblings`, so the
  hosted line stays as it is.
- Stops, detail panels or links for a sibling's stories on the line. The chapter keeps them.
- Any change to the record, `with_across` or the sibling reads.
- New colour tokens.
- The line's scroll position on a phone. jsdom has no layout to test it in, and it isn't changed.

## Approval handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants the lane `push` and `draftPullRequests`.

## Execution posture and sensors

Test-first. Sensors:

- `tests/test_delivery_views_page.py`, which runs `tests/delivery_views_page/` under Node with `jsdom`: a new
  `line-map-epic-post.test.mjs`, plus the "Also linked" row in `line-map-siblings.test.mjs`.
- `golden.test.mjs`, which must stay unchanged, for the no-sibling path.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check` for the delivery-views outputs and the
  catalog entry. The digest refresh in `provenance.lock.json` is a regenerable listing, so the lane refreshes it with
  the writer and doesn't own it as a whole file.
- The full suite under its budget at the final validation.

The new module reuses `support.mjs`'s `openPage` and `withAcross`, opens one page per state, and adds no per-test
fixture build. Layout that jsdom lacks is stubbed in the module itself, through a `{ run }` step followed by a push,
because the lane doesn't own `support.mjs`.

## Scenarios

In `tests/delivery_views_page/line-map-epic-post.test.mjs`. Every state is opened in light and in dark
(`data-theme`), and this repository's own pieces are stated in each one.

- **No same-epic sibling.** First no siblings at all, then only an other-epic group. The SVG's markup, width, viewBox,
  labels, counts and `aria-label` equal today's, and `--merge-width` is 176px with `--merge-top` 16px.
- **Waiting.** This repository has landed everything, and one fresh sibling has open stories. Both posts are drawn,
  "Finish line" and the epic's, with the epic's text above its post. The sibling's turn is dashed and the epic's line
  is dashed. The count covers both repositories, and `--merge-width` is 304px.
- **Mixed epics.** One fresh same-epic sibling with open stories, plus one other-epic group. Exactly one sibling row
  and one turn are drawn. The label isn't "Epic ?", and the line and post aren't in the unreadable style. The epic's
  count and the `aria-label` leave the other-epic group out. The chapter lists it under "Also linked", after the drawn
  group, with its reason.
- **Met.** This repository has landed everything, every sibling story has landed, and every sibling is fresh. Each
  sibling turn is solid, and so are the epic's line and post.
- **Nearly met.** Every sibling story has landed and every sibling is fresh, but this repository has one open piece.
  Each sibling turn is solid. The epic's line stays dashed, and its count reads one short of its total.
- **One unreadable.** One stale sibling. Its turn is dotted, and its row reads "Couldn't be read since <since>" with
  the fixture's exact time. The epic's line and post are dashed, the label is "Epic ?" with the sibling's name under
  it, and the `aria-label` names it.
- **Two unreadable.** One stale sibling, plus one attempted but never read (`readAt` null, `since` set). "2 can't be
  read" is under the label. The first row reads "Couldn't be read since <since>" and the second "Never read".
- **Many, and the ranking.** There are five same-epic siblings. The waited-on one has the highest landed share, so
  only the wait puts it first. Two share the lowest share and differ in open stories, and the one with more open
  stories is drawn second. One is fresh with no stories and folds into the chip. The chip reads "+3 more" and names
  the three, and the epic's count and the `aria-label` include all five and the folded number. A pushed document then
  makes the other lowest sibling the furthest behind and changes the epic's count. On that redraw the second row
  changes, and the count flips through `figureText`.
- **The stylesheet.** Read as rule text, as `line-map-plan.test.mjs` reads its own:
  - Each new class sets `stroke: var(--edge)`: the sibling turn, the epic line and the epic post, with their open and
    unreadable variants.
  - The dashed and dotted variants set different, non-empty `stroke-dasharray` values, and the solid ones set none.
  - `.lines-track`'s padding and both least widths use `var(--merge-width)`, and none still holds `176px`, `420px`
    or `292px`.
  - The phone rule keeps the `104px` column.
  - Every token the new rules use is defined in `:root` and in both dark rules, and none is `--held` or `--wait`.
  
  In each state above, the elements carry the classes those rules match, and every rule's selector matches at least
  one drawn element.
- **Headroom.** A one-lane record with one stale sibling. For every text of the epic segment, its `y` plus
  `--merge-top`, less its font size, is at least 0, measured from the track's top.
- **Motion.** The Waiting state with a check running, under `motion`. A `{ run }` step gives the track a
  `clientWidth` and `clientHeight`, and gives the track and each stop button a `getBoundingClientRect`, before a push
  redraws the page. The `flow-trunk` path ends at this repository's post's x (the track's width less 304, plus 162),
  not at the epic's post. Its y is the drawn trunk's.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3630 | `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css`, `skills/delivery-views/SKILL.md`, `catalog.json`, `tests/delivery_views_page/line-map-epic-post.test.mjs`, `tests/delivery_views_page/line-map-siblings.test.mjs` |

## Stories

### V26-3630. Draw the epic's post on the line, with sibling repositories turning in

Lane 1, tier 1, not blocked. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/delivery-views/SKILL.md`, `catalog.json`,
`tests/delivery_views_page/line-map-epic-post.test.mjs` and `tests/delivery_views_page/line-map-siblings.test.mjs`.
