---
title: First-class seats in the shared-delivery views - Plan
type: feat
date: 2026-11-26
topic: first-class-seats
reviewSlug: first-class-seats
execution: code
status: approved
---

# First-class seats in the shared-delivery views - Plan

## Goal

On a shared delivery record (one with `meta.collab`, several seats on several
machines), every word a person reads that names an actor says whose it is. The
seat that owns a call reads "you" or "the lead", as today. Every other seat reads
the owner's label ("Ben", "Ben's lead"). Nobody reads "you" for someone else's
call, and only your own call takes over Now. A record nobody shares draws byte
for byte as it does today.

This plan answers V26-3959. It changes nothing by itself: the stories below are
created as tracked items once the plan is approved.

## Scope

- In:
  - the supervisor's Needs you list: each item's owning scope and seat, and its
    words for each kind of reader;
  - the local page's knowledge of its own seat, and the hosted page's lack of one;
  - Now's sentence, the marks, the Needs you box and the rejoin's "you" words on
    the line map;
  - a seat chip and a "shared with" line on a shared record's page;
  - the delivery-views and orchestration wording rules for shared records.
- Out:
  - a blocker resolved on one seat that stays listed on another, because the
    resolutions log is machine-local;
  - a tracker lag item on a seat without `delivery`, which reads that machine's
    own empty ledger;
  - the listener item naming lanes another seat holds;
  - Needs you kinds the docs promise but nothing emits: a held peer partition, a
    seat split, a generation mismatch, a pending seat or scope request;
  - V26-3930 (settle state on a delivery move) and V26-3919 (deferred test
    strength);
  - tracker posts, the friction note and every lead-facing text (`text`,
    `clears`, command output). Their readers are the lead or the tracker, not a
    seat's person.

  The four bugs are filed as V26-3997 (the resolutions log), V26-3998 (the tracker
  lag), V26-3999 (the listener) and V26-4000 (the promised kinds). This plan's stamping does not hide them, and does
  not depend on them.

## Decisions recorded here

The operator approved this plan on 2026-11-26, with decisions 1 to 11 as written.

Operator calls the ticket asked for, with the recommendation adopted. The
operator confirms or overturns them in approving this plan.

1. **An item no scope raised belongs to the queue holder.** Drafts to mark ready
   are the only such operator item today, and the queue holder is the one whose
   merge step refuses them.
2. **A seat that can't be resolved reads by role only.** A person reads
   "Someone", a lead "The lead". Such an item never reads "you", never takes over
   Now and never marks "· you".
3. **The hosted page has no seat.** It names every owner by label and nobody as
   "you". It shows no seat chip.
4. **The rejoin's person words are the delivery holder's.** "waits for your
   merge", "It's your call to accept" and "The merge is the operator's" all
   belong to the seat holding `delivery`. The integration's change request, its
   full suite and its review are the delivery's finish, not the merge queue's.
   This corrects the recommendation given on 2026-11-26 before the inventory,
   which gave the merge to the queue holder.
5. **Compounding is the lead's work**, in the `delivery` scope, as the code has
   it. It reads "The lead" on the delivery holder's page and "Ama's lead"
   elsewhere.
6. **The seat chip and the "shared with" line ship with the page change**, in
   the same story. Without them a shared page's "you" doesn't say whose page it
   is.
7. **"The lead" stays unnamed outside Needs you and Now.** The 52 actor-word
   sites on the page (inventory below) are not each given a seat. Only the words
   that decide what the reader does are seat-aware: Needs you rows, the Needs you
   summary, Now's sentence and its marks, and the rejoin's person words. Every
   other "the lead" stays as written, because the Seats chapter already says once
   who holds what (one statement per fact).
8. **The supervisor words each item; the page doesn't.** Headlines embed the
   actor mid-sentence ("Lane 2 waits on the lead"), so the page can't swap a
   name in. Each item carries its owner seat as stamped on the pass, and three
   wordings:
   - `said`, for the seat that wrote the list;
   - `saidAll`, for a reader with no seat, with every owner named by label;
   - `saidRole`, by role only, as decision 2 has it ("Someone", "The lead").

   Each seat's local page reads only its own list, so `said` is right there.
   The hosted page, fed by the delivery holder's list, reads `saidAll`. This
   replaces the 2026-11-26 recommendation that the page work out each owner
   itself. That only works for words the page writes, so it applies only to the
   rejoin (decision 4).
9. **A stamp the record has since outrun reads by role.** The page checks each
   item's stamped seat against the holder the journal names now, using the
   same `holderOf` the Seats chapter uses. If they differ, a scope moved after
   the pass. On every page, the hosted one included, the item then reads
   `saidRole`, takes nothing over and never says "you". The next pass words it
   again. A `seat:<id>` owner is held by `<id>` and is never outrun.
10. **The page tells shared from unshared by `meta.collab`, not by a viewer.**
    On an unshared record, "operator means you" stays exactly as today. On a
    shared record, an item is yours only when it is a person's call
    (`who: operator`), its stamped seat is the viewer's, and that seat still
    holds the item's owner. A shared page with no viewer, or an item with no
    stamped seat, has nothing that is yours.
11. **The summary is the pass's.** `summary` and `summaryAll` are worded when
    the list is written. An item that later reads as outrun still counts in
    them until the next pass, the same lag as every other word in the list.

## Inventory and disposition

Owner is the seat holding the scope named. "Seat-aware" means it reads
differently on the owner's page, on another seat's page and on the hosted page.

### Needs you items (supervisor)

| Kind | Owner scope | Note |
| --- | --- | --- |
| `trackerLedger`, `trackerLag`, `compounding`, `regrant`, `laneOutside`, `sibling`, `free`, `paused` | `delivery` | `meta.hold`, `laneRegions`, `siblings` and assignment are delivery writes |
| `healthRunning`, `healthFailed`, `healthFlaky`, `baseHealth`, `baseBroken`, `baseFocused`, `runnerHold`, `drafts`, `report`, `stale` | `queue` | `report` and `stale` read `meta.sourceReports` and `meta.sources`, queue-only fields. The ticket's table had them as delivery. |
| `unaccepted`, `stopped`, `laneBlocked`, `laneAsks` | `lane:<lane>` | `lead-accept` requires the lane scope; the lane asks its own holder |
| `feedback` | `plan:<anchor>` | an unheld section falls to the delivery holder, as `delivery_state.holder` has it |
| `flapping`, `listener`, `laneUnreadable` | `seat:<writer>` | each reports what this machine can't do or read |
| `blocker` | `lane:<lane>` of its lane, else of its subject, else `seat:<origin.seat>` | taken from the journal entry's `origin`, never its local id |

`seat:<id>` is a new owner form for this machine's own items. A scope names a
part of the work; `seat:` names who saw it.

### Page words (line map), seat-aware

| Site | Today | Owner |
| --- | --- | --- |
| Now override (`nowState`) | first current `who: operator` item takes the sentence; its mark reads "· you"; fact "<mark> waits on you." | takes over only when the item is yours (decision 10) |
| Mark suffix (`nowBlock`) | names "you" or "the forge" | adds another seat's label for a person's call ("Merge · Ben") |
| Needs you rank and rows (`needsYou`) | operator items first; `said.who` | the viewer's own calls first, then others'; rows show `said`; `saidRole` when outrun (decision 9); `saidAll` with no viewer |
| Needs you summary | "N things need you" | shared form: "1 thing needs you; 1 more needs Ben" |
| Rejoin "waits for your merge", "It's your call to accept" (`rejoinStage`) | you | `delivery`, worded on the page |
| Rejoin ledger "The merge is the operator's." | operator | `delivery`, worded on the page |

### Page words, unchanged on purpose

Every other site the inventory found keeps its words (decision 7). That covers
the rejoin's "The lead is …" sentences, the checks sheet's "chosen by the lead",
the stage words "waiting for the lead to accept", the close's "The lead finishes
it …", "Hidden while updates are stopped, until the lead restarts them", the
plan chapter's approval line, the What-happened ledger and the Who chapter. The
Seats chapter's own label words are already seat-aware.

The full site list, with file and line as of `main` at `2ac337ca`, is in
`docs/plans/2026-11-26-first-class-seats.inventory.md`.

## Contracts

### The Needs you list (`delivery-needs-you/1`, unchanged version)

On a shared record only:
- each item gains:
  - `scope`: `delivery`, `queue`, `lane:<id>`, `plan:<anchor>` or `seat:<id>`;
  - `seat`: the seat holding that scope at the pass, absent when unresolved;
  - `saidAll` and `saidRole`, the same shape as `said`;
- the list gains `summaryAll`, and its `summary` is worded for the writing seat.

Within the pass, the order is: `say_needs` fills each item's `lane` from its
piece, then the item is stamped, then it is worded. So `stopped` and
`unaccepted` items find their lane, and the words know the owner. An item with
no lane where its kind wants one falls back to `delivery`.

On an unshared record none of these keys is written, so `needs-you.json` and
the supervisor log are byte-identical to today's. V26-3994 adds the pin that
proves it. The version stays at 1: every added member is optional, and an
unshared list never carries one.

### What the page is given

- `render.py` embeds the viewer's seat id from `local_seat`, only when the record
  is shared and the seat resolves. The page takes the viewer's label from the
  record's seats with `seatLabels`, as the Seats chapter does.
- `render.py`'s row allowlist passes `scope`, `seat`, `saidAll` and `saidRole`,
  and the list passes `summaryAll`.
- `hosted.py` embeds no viewer. The hosted page reads `saidAll` and `summaryAll`,
  or `saidRole` per decision 9.
- `holderOf` resolves an unheld `plan:<anchor>` to the delivery holder, matching
  `delivery_state.holder`; today it returns the first seat. It treats
  `seat:<id>` as held by `<id>`.

### The seat chip

Shown only on a shared record's local page with a resolved viewer: "<label> ·
holds delivery · lane 2", listing only the scopes held, then "Shared with Ben".
The hosted page shows "Shared by Ama and Ben" and no chip. An unshared page shows
neither.

## Finish line

1. On the two-seat fixture, the same record, drawn as each seat, gives:
   - each person's own call in Now's sentence and as "· you" on its mark;
   - the other's call under the other's label, in the quieter style;
   - a lane's call to its lead as "the lead" on its holder's page and "Ama's
     lead" on the other, taking nothing over on either;
   - the rejoin's "your merge" and "your call to accept" on the delivery
     holder's page, and "Ama's merge" and "Ama's call to accept" on the other;
   - the summary "1 thing needs you; 1 more needs Ben" on a, and the mirror on b.
2. An item whose seat can't be resolved, or whose stamp the journal has outrun,
   reads by role only on every page and takes nothing over, even on a page with
   a viewer.
3. The page drawn with no viewer, at the rejoin stage, shows every owner by
   label and says "you" nowhere in Needs you, Now or the rejoin. The hosted
   page is that page.
4. Every unshared record draws as today:
   - the new unshared list pin (V26-3994) passes;
   - the ten unshared line-map goldens and the unshared Now and Needs you page
     tests pass unchanged.
5. `skills/delivery-views/SKILL.md` and the orchestration guide state the rule
   once: on a shared record, "you" is the seat that holds the call.

## Stories

The stories sit under the epic V26-3993. Earlier drafts of this plan called them
`SEATS-1` to `SEATS-3`.

### V26-3994. Stamp each Needs you item with its owner and word it for each reader

Lane 1, tier 1, not blocked. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/scripts/tracker_publisher.py`,
`skills/orchestrate-work/references/wait-words.json`,
`skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/SKILL.md`, `tests/test_waits.py`,
`tests/test_collab_colead_runtime.py` and
`tests/test_run_delivery_supervisor.py`.

- A `NEED_SCOPES` table beside the words table gives each kind its owner, as in
  the inventory.
- One seat resolver per pass, built from one journal read, stamps `scope` and
  `seat` after `say_needs`, only on a shared record.
- `say_needs` words each item for a viewer seat: `said` for the writing seat,
  `saidAll` for none, `saidRole` by role. `wait-words.json` gains the
  other-seat person, the other seat's lead and "Someone" for `who` and `whom`,
  and the shared summary.
- Tests, each shown red against the mutant named:
  - **Unshared list.** `needs_you()` on an unshared record emitting an item of
    every owner form: every item's key set is exactly today's, and the list has
    no `summaryAll`. Mutant: the `meta.collab` guard removed.
  - **Owner table.** `NEED_SCOPES` pinned as one literal mapping, kind to owner
    form. Mutant: `report` and `stale` mapped to `delivery`.
  - **Completeness.** The pinned mapping covers every kind emitted, read from
    every producer of the list: `run_delivery.py` and `tracker_publisher.py`.
    Mutant: a new kind in `tracker_publisher.py` with no row.
  - **Blockers.** Three cases: a blocker with a lane, one with only a subject
    whose piece is on a lane, and one with neither (`seat:<origin.seat>`).
    Mutant: the subject branch skipped.
  - **No lane.** A `stopped` item whose piece has no lane, on the two-seat
    record, stamps `delivery`/a. Mutant: the fallback stamping `queue`.
  - **Feedback.** An unheld section after `delivery` has moved off the first
    seat stamps the delivery holder. Mutant: the first seat.
  - **Wording.** On a shared list, `said`, `saidAll` and `saidRole` for
    scenarios 1, 2 and 4, plus `summary` and `summaryAll` for scenario 3.
  - **Two seats.** One case in `FullHealthAcrossSeatsTests`, which already gives
    the queue to b, so no new fixture is built: both seats' lists stamp a draft
    `queue`/b.
- Pins to re-derive in the same story:
  - `test_waits.py`'s source-layout check of producer dicts (around line 621)
    and its exact template key set (`TableTests`);
  - the whole-`said` comparisons and summary sentences in `test_waits.py`;
  - the supervisor test's `said.who` check.

  Each is re-derived, not loosened. The stamp is added after the producer dict,
  so the source-layout check keeps its pattern.

### V26-3995. Give the local page its seat, and pass the stamps through

Lane 2, tier 1, not blocked. Test-first. Files:
`skills/delivery-views/scripts/render.py`,
`skills/delivery-views/scripts/hosted.py`,
`tests/test_delivery_views_render.py`,
`tests/test_delivery_views_hosted.py` and
`tests/test_delivery_views_hosted_projection.py`.

- The viewer embed, the allowlist additions and the hosted wording choice, as
  in Contracts.
- Tests:
  - The exact row key pin (around line 1083) gains the new keys for a shared
    list.
  - A key outside the allowlist is still dropped.
  - An unshared record's page embeds no viewer, even when the checkout has a
    local seat. Mutant: the shared check removed.
  - A shared record's local page embeds the seat id, and the hosted page embeds
    none.
- Not blocked by V26-3994: the contract above fixes the list's shape, and the
  tests use hand-written lists.

### V26-3996. Draw seats on Now, the marks, Needs you and the rejoin

Lane 2, tier 2, after V26-3995. Test-first. Files:
`skills/delivery-views/assets/line-map.js`, `skills/delivery-views/SKILL.md`,
`tests/delivery_views_page/` and `tests/fixtures/delivery-views/line-map/`.

- A `yours(item)` helper, as decision 10 defines it, replaces "operator means
  you" in `nowState`, `nowBlock` and `needsYou`. Rank: holding first, then
  yours, then the rest.
- Marks add another seat's label for a person's call ("Merge · Ben").
- Rows read `said`, or `saidRole` when outrun; with no viewer, `saidAll`.
- The rejoin's person words are worded on the page for the delivery holder.
- The `holderOf` fixes, the seat chip and the "shared with" line.
- A page-test fixture, made from the `colead` case's document, at an
  integration-base rejoin stage, with two seats' Needs you lists.
- Page cases, each shown red against the mutant named:
  - drawn as a and as b: finish line 1. Mutant: rejoin worded for the queue
    holder.
  - a `who: lead` item stamped with the viewer's seat takes nothing over.
    Mutant: `yours` without the `who` test.
  - with a viewer, an operator item with `scope` and no `seat` reads "Someone",
    takes nothing over and ranks with the others'. Mutant: an absent seat
    falling back to "operator means you".
  - an outrun stamp reads `saidRole`. Mutant: `saidAll`.
  - a lane-less `blocker` (`who: lead`, as its producer emits it) stamped
    `seat:b`, drawn as b where b is not the first seat, reads `said`, not
    `saidRole`, and takes nothing over; drawn as a, it reads under b's label.
    Mutant: `seat:` judged by `holderOf`'s first-seat default.
  - `delivery` moved from a to b, then a `feedback` item on an unheld section
    stamped b: b's call on b's page, under b's label on a's. Mutant: `holderOf`
    unchanged.
  - drawn with no viewer: finish line 3. Mutant: the hosted page reading `said`.
  - the unshared Now fixtures (`now-fixtures.mjs`) and the Needs you rank pass
    unchanged.
- The `colead` and `plan-shared` goldens are recaptured. The ten unshared
  goldens are untouched.
- Comes after V26-3995 because it takes over V26-3995's lane, and its cases assert
  the embed V26-3995 writes. Not blocked by V26-3994.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3994 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/references/`, `tests/test_waits.py`, `tests/test_collab_colead_runtime.py`, `tests/test_run_delivery_supervisor.py` |
| 1 | 2 | V26-3995 | `skills/delivery-views/scripts/`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted.py`, `tests/test_delivery_views_hosted_projection.py` |
| 2 | 2 | V26-3996 | `skills/delivery-views/assets/`, `skills/delivery-views/SKILL.md`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/line-map/` |

Shared files: `provenance.lock.json` and each touched skill's catalog entry
change in every story. The merge queue serializes them. A conflict only in the
lock is resolved by taking either side and running the provenance writer, as
`AGENTS.md` says. No story uses a fixed port or browser session. The page lane
runs under Node from the checkout's own `node_modules/`.

## Test scenarios

Two seats: a (Ama) holds delivery and lane 2; b (Ben) holds the queue and
lane 1.

1. A draft waits to be marked ready.
   - Both lists stamp `queue`/b.
   - b's `said` reads "You"; a's reads "Ben".
   - Both lists' `saidAll` read "Ben"; `saidRole` reads "Someone".
2. Lane 2 asks its lead.
   - a reads "Lane 2 waits on the lead"; b reads "Lane 2 waits on Ama's lead".
   - No "you" on either page, and nothing takes over Now.
3. One call each, at the rejoin stage:
   - each page leads with its own call and lists the other's under the other's
     label;
   - the summary reads "1 thing needs you; 1 more needs Ben" on a and the mirror
     on b;
   - the rejoin is Ama's: "your merge" on a, "Ama's merge" on b.
4. Unresolved: an item with no `seat`, drawn as a with a viewer, reads
   "Someone" and takes nothing over.
5. An outrun stamp: the queue moves from b to a after b's pass. On b's page,
   and with no viewer, the drafts item reads `saidRole` and takes nothing over.
   a's next pass reads "You".
6. Blockers: one on lane 1 stamps `lane:lane-1`/b; one with only a subject on
   lane 2 stamps `lane:lane-2`/a; one the supervisor journaled on b with
   neither stamps `seat:b`/b, reads `said` on b's page and takes nothing over.
   A `stopped` piece with no lane stamps `delivery`/a.
7. Feedback after `delivery` moves from a to b, on an unheld section, is b's.
8. Unshared: every item's key set and the list's keys are today's; the page,
   the goldens and the Now cases are unchanged.
9. No viewer (the hosted page): owners by label, "Shared by Ama and Ben", no
   chip, and no "you" in Needs you, Now or the rejoin.

## Execution posture

`test-first` for every story. Each new behaviour has a clear expected outcome,
and each test above names the mutant it must fail on.

## Sensors

- V26-3994: `tests.test_waits`, `tests.test_collab_colead_runtime` and
  `tests.test_run_delivery_supervisor`.
- V26-3995: `tests.test_delivery_views_render`, `tests.test_delivery_views_hosted`
  and `tests.test_delivery_views_hosted_projection`.
- V26-3996: `tests/test_delivery_views_page.py` (the Node page lane, including
  `golden.test.mjs`'s byte-for-byte recapture check).
- Every story: `scripts/validate-registry.py` and
  `scripts/update-provenance.py --check`.
- Before any release that carries this: the full suite with
  `--workers 4 --budget tests/suite-budget.json` on Python 3.11, per
  `AGENTS.md`. Added tests are pure functions, page cases or one case in an
  existing fixture class, so the budget is kept without raising a ceiling.

## Approval handoffs

1. The operator approves this plan, including decisions 1 to 11.
2. The stories and the four out-of-scope bugs are created with
   `$create-linear-ticket`, under V26-3959's project.
3. Delivery runs under `orchestrate-work`. No release is cut by this plan.

## Compounding

A solution note on who "you" is on a shared record: a word that names an actor
is worded where the actor is known, and checked where the reader is known. A
corpus pin that every `needsYou` `who`/`whom` case has an other-seat form.
