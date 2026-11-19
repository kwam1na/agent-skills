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

## Finish line

- Both plan lenses align.
- V26-3630 has landed on `main` through the merge queue, and every acceptance criterion on the ticket holds.
- A record with no same-epic sibling draws the line exactly as before. Every golden is unchanged, and the drawing is
  still 176px wide.
- The full local suite passed on macOS with Python 3.11 at the base head after V26-3630 landed, run with
  `--workers 4 --budget tests/suite-budget.json`. It stayed inside the budget.
- This delivery doesn't tag a release. The change is additive, so a minor version.

## Decisions

1. **What is drawn.** The line reads only `doc.across.groups`. A group is a same-epic sibling unless its
   `lastOutcome` is `other-epic`. The record's own comparison of `trackerParent` is the ticket's "matches this
   delivery's". A group under another epic draws nothing on the line. The chapter names it under an "Also linked" row,
   after the drawn groups, with its reason. With no same-epic sibling, `mergeDrawing` takes today's path unchanged.
2. **Which two are drawn** (operator, 2026-11-18). There are at most two sibling rows. First comes the sibling that
   holds a story this repository waits on, meaning one whose story key is the `on` of a `cause: "sibling"` wait in
   `doc.waits`. Then comes the sibling furthest behind: the lowest share of landed stories, then the most open stories,
   then the record's order. A sibling with no stories counts as furthest behind only when it is unreadable. The rest
   fold into a "+N more" chip on the epic's post. The chip's accessible name lists the folded siblings. The order is
   decided again on every draw.
3. **Two posts** (operator, 2026-11-18). This repository's "Finish line" post keeps its place, label and counts. Past
   it the main line runs on to the epic's post. The epic's label and count sit above that post, because sibling turns
   come in from below. The drawing widens from 176px to 304px. Every place the 176 appears (`drawFlow`'s
   `mergeX`/`joinX`/`finishX`, `.lines-track`'s padding and its least widths, wide and phone) reads one width. The
   drawing sets that width as a property on the track, so the stylesheet and the motion can't disagree.
4. **The epic's count.** It counts this repository's pieces and every same-epic sibling's stories from its last kept
   read, drawn or folded, as "L/T landed". The epic's line is solid only when every one of those has landed and every
   same-epic sibling is fresh.
5. **Sibling rows.** A row goes under this repository's lanes, on the same 84px pitch, with its stop 23px down. It
   shows the sibling's name and "L of T landed", and has no stop buttons; the chapter holds its stories. The row turns
   in at the epic's post from below. The turn is drawn neutral, in `--edge`. It is solid once all of the sibling's
   stories have landed, and dashed before that.
6. **An unreadable sibling** (operator, 2026-11-18). A same-epic group whose `fresh` is not `true` is unreadable:
   stale, unavailable, incompatible or never read. Its row's turn is dotted. Its row reads "Couldn't be read since
   <time>", from the group's `since`, or "Never read". The epic's line and post go dashed in `--edge`, and the label
   reads "Epic ?", with the unreadable sibling named under it, or "N can't be read" when more than one is. No `--held`
   or `--wait` colour is used, and no new colour token is added.
7. **What the drawing says.** Its `aria-label` keeps today's sentence and adds the epic's count, for example "the
   epic: 5 of 9 pieces landed across 3 repositories". It names each unreadable sibling and the number folded into the
   chip. The epic's figures go through `figureText`, like this repository's, so a changed count is announced the same
   way.
8. **The motion.** `drawFlow` stops the trunk stream at this repository's post, as it does today. Nothing flows
   toward the epic's post, because no check of this repository runs there.

## Out of scope

- The hosted page, which stays with V26-3605. The public projection drops `siblings`, so the hosted line stays as it
  is.
- Stops, detail panels or links for a sibling's stories on the line. The chapter keeps them.
- Any change to the record, `with_across` or the sibling reads.
- New colour tokens.

## Approval handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants the lane `push` and `draftPullRequests`.

## Execution posture and sensors

Test-first. Sensors:

- `tests/test_delivery_views_page.py`, which runs `tests/delivery_views_page/` under Node with `jsdom`: a new
  `line-map-epic-post.test.mjs`, plus the "Also linked" row in `line-map-siblings.test.mjs`.
- `golden.test.mjs`, which must stay unchanged, for the no-sibling path.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check` for the delivery-views outputs. The
  digest refresh in `provenance.lock.json` is a regenerable listing shared by lanes, so no lane owns it as a whole
  file.
- The full suite under its budget at the final validation.

The new test module reuses `support.mjs`'s `openPage` and `withAcross`, and opens one page per state. It adds no
fixture build that runs per test.

## Scenarios

In `tests/delivery_views_page/line-map-epic-post.test.mjs`, each state in light and in dark (`data-theme`):

- No siblings, and siblings all under another epic: the SVG's markup, width, viewBox, labels, counts and
  `aria-label` equal today's, and the track's padding is 176px.
- Waiting: one same-epic sibling with open stories. Two posts are drawn, "Finish line" and the epic's, with the
  epic's label above its post. The sibling's turn is dashed, and the epic's count covers both repositories.
- Met: every sibling story landed and every sibling fresh. Each sibling turn is solid, and so is the epic's line.
- Unreadable: one sibling stale, and one never read. The turns are dotted, the epic's line and post are dashed, and
  the label is "Epic ?" with the sibling named. No element of the epic segment computes a stroke or fill from
  `--held` or `--wait`, in light or dark. The `aria-label` names the sibling.
- Many: four same-epic siblings, one holding a story this repository waits on. That one is drawn first and the
  furthest behind second. The chip reads "+2 more" and names the other two, and the epic's count includes all four.
- Other epic: a group with `lastOutcome: "other-epic"` draws no row or turn, and the chapter shows it under "Also
  linked".
- Width: with siblings the drawing is 304px, and the track's padding and least width grow by 128px. The phone layout
  keeps its 104px name column, and the line opens scrolled to its end.
- Motion: with a check running, the trunk stream ends at this repository's post.

Every new rule in `line-map.css` uses only tokens defined in `:root` and in both dark rules, checked as
`line-map-plan.test.mjs` already checks its own.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3630 | `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css`, `tests/delivery_views_page/line-map-epic-post.test.mjs`, `tests/delivery_views_page/line-map-siblings.test.mjs` |

## Stories

### V26-3630. Draw the epic's post on the line, with sibling repositories turning in

Lane 1, tier 1, not blocked. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `tests/delivery_views_page/line-map-epic-post.test.mjs` and
`tests/delivery_views_page/line-map-siblings.test.mjs`.
