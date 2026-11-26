---
title: First-class seats in the shared-delivery views - Plan
type: feat
date: 2026-11-26
topic: first-class-seats
reviewSlug: first-class-seats
execution: code
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

  Each out-of-scope bug above is filed as its own tracked item when this plan is
  approved. This plan's stamping does not hide them, and does not depend on them.

## Decisions recorded here

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
   name in. Each item carries its owner seat as stamped on the pass, and two sets
   of words:
   - `said`, worded for the seat that wrote the list;
   - `saidAll`, worded for a reader with no seat.

   Each seat's local page reads only its own list, so `said` is right there.
   The hosted page, fed by the delivery holder's list, reads `saidAll`. This
   replaces the 2026-11-26 recommendation that the page derive each owner
   itself. That only works for words the page writes, so it applies only to the
   rejoin (decision 4).
9. **A stamp the record has since outrun reads as unresolved.** The page checks
   each item's stamped seat against the holder the journal names now, using the
   same `holderOf` the Seats chapter uses. If they differ, a scope moved after
   the pass: the item reads `saidAll`, takes nothing over and never says "you".
   The next pass words it again.

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
| Now override (`nowState`) | first current `who: operator` item takes the sentence; its mark reads "· you"; fact "<mark> waits on you." | the item's stamped seat; takes over only when it is the viewer's |
| Mark suffix (`nowBlock`) | names "you" or "the forge" | adds another seat's label for a person's call ("Merge · Ben") |
| Needs you rank and rows (`needsYou`) | operator items first; `said.who` | the viewer's own calls first, then others'; rows show `said`, or `saidAll` per decision 9 |
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
- each item gains `scope` (`delivery`, `queue`, `lane:<id>`, `plan:<anchor>` or
  `seat:<id>`), `seat` (the seat holding it at the pass, or absent when
  unresolved) and `saidAll`, the same shape as `said`;
- `summary` is worded for the writing seat, and `summaryAll` for no seat.

On an unshared record none of these keys is written, so `needs-you.json` and the
supervisor log are byte-identical to today's. The version stays at 1: every
added member is optional, and an unshared reader never sees one.

### What the page is given

- `render.py` embeds the viewer's seat, `{seat, label}` from `local_seat`, only
  when the record is shared and the seat resolves. Without it the page has no
  viewer and treats every owner as another's.
- `render.py`'s row allowlist passes `scope`, `seat` and `saidAll`, and the list
  passes `summaryAll`. On an unshared record there is nothing extra to pass.
- `hosted.py` embeds no viewer, and the page reads `saidAll` and `summaryAll`.
- `holderOf` resolves an unheld `plan:<anchor>` to the delivery holder, matching
  `delivery_state.holder`. Today it returns the first seat.

### The seat chip

Shown on a shared record's local page with a resolved viewer: "<label> · holds
delivery · lane 2", listing only the scopes held, then "Shared with Ben". On the
hosted page: "Shared by Ama and Ben", with no chip. Never shown unshared.

## Finish line

1. On the two-seat fixture, the same record, drawn as each seat, gives:
   - each person's own call in Now's sentence and as "· you" on its mark;
   - the other's call under the other's label, in the quieter style;
   - a lane's call to its lead as "the lead" on its holder's page and "Ama's
     lead" on the other.
2. An item whose seat can't be resolved, or whose stamp the journal has outrun,
   reads by role only on both pages and takes nothing over.
3. The hosted page shows every owner by label and says "you" nowhere in Needs
   you, Now or the rejoin.
4. The ten unshared line-map goldens, the unshared `needs-you.json`
   byte-identity check and the unshared render pins pass unchanged.
5. `skills/delivery-views/SKILL.md` and the orchestration guide state the rule
   once: on a shared record, "you" is the seat that holds the call.

## Stories

Story keys are given when the items are created; the placeholders `SEATS-1` to
`SEATS-3` stand in for them.

### SEATS-1. Stamp each Needs you item with its owner and word it for each reader

Lane 1, tier 1, not blocked. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/references/wait-words.json`,
`skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/SKILL.md`, `tests/test_waits.py`,
`tests/test_collab_colead_runtime.py` and
`tests/test_run_delivery_supervisor.py`.

- A `NEED_SCOPES` table beside the words table gives each kind its owner, as in
  the inventory. A test fails if any emitted kind is missing from it.
- One seat resolver per pass, built from one journal read, stamps `scope` and
  `seat` in `needs_you()`, only on a shared record.
- `say_needs` words each item for a viewer seat: `said` for the writing seat,
  `saidAll` for none. `wait-words.json` gains the other-seat person, the other
  seat's lead and "Someone" for `who` and `whom`, and the shared summary.
- Pins to re-derive in the same story:
  - `test_waits.py`'s source-layout check of producer dicts (around line 621)
    and its exact template key set (`TableTests`);
  - the whole-`said` comparisons and summary sentences in `test_waits.py`;
  - the supervisor test's `said.who` check.

  Each is re-derived, not loosened: the source-layout check learns the stamp's
  position, or the stamp is added after the producer dict.

### SEATS-2. Give the local page its seat, and pass the stamps through

Lane 2, tier 1, not blocked. Test-first. Files:
`skills/delivery-views/scripts/render.py`,
`skills/delivery-views/scripts/hosted.py`,
`tests/test_delivery_views_render.py`,
`tests/test_delivery_views_hosted.py` and
`tests/test_delivery_views_hosted_projection.py`.

- The viewer embed, the allowlist additions and the hosted `saidAll` choice, as
  in Contracts.
- The exact row key pin (around line 1083) gains the new keys for a shared list
  only. A second assertion pins the unshared key set as today's.
- Not blocked by SEATS-1: the contract above fixes the list's shape, and the
  tests use hand-written lists.

### SEATS-3. Draw seats on Now, the marks, Needs you and the rejoin

Lane 2, tier 2, after SEATS-2. Test-first. Files:
`skills/delivery-views/assets/line-map.js`, `skills/delivery-views/SKILL.md`,
`tests/delivery_views_page/` and `tests/fixtures/delivery-views/line-map/`.

- A `yours(item)` helper replaces "operator means you":
  - true only when the item's stamped seat is the viewer's and still holds its
    scope;
  - false with no viewer, so on the hosted page.
- `nowState`, `nowBlock` and `needsYou` use it, along with the seat label on
  marks and the rank rule.
- The rejoin's person words are worded for the delivery holder.
- The `holderOf` plan-section fix, the seat chip and the "shared with" line.
- Tests:
  - a page test that draws one shared fixture as seat a, as seat b, with no
    viewer and with an outrun stamp;
  - the `colead` and `plan-shared` goldens recaptured;
  - the ten unshared goldens untouched.
- It comes after SEATS-2 because it takes over SEATS-2's lane files list, and
  its page test asserts the embed SEATS-2 writes. It is not blocked by SEATS-1.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | SEATS-1 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/references/`, `tests/test_waits.py`, `tests/test_collab_colead_runtime.py`, `tests/test_run_delivery_supervisor.py` |
| 1 | 2 | SEATS-2 | `skills/delivery-views/scripts/`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted.py`, `tests/test_delivery_views_hosted_projection.py` |
| 2 | 2 | SEATS-3 | `skills/delivery-views/assets/`, `skills/delivery-views/SKILL.md`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/line-map/` |

Shared files: `provenance.lock.json` and each touched skill's catalog entry
change in every story. The merge queue serializes them, and a conflict only in
the lock is resolved by taking either side and running the provenance writer,
as `AGENTS.md` says. No story uses a fixed port or browser session. The page
lane runs under Node from the checkout's own `node_modules/`.

## Test scenarios

1. Two seats, a holds delivery and lane 2, b holds the queue and lane 1. A draft
   waits to be marked ready.
   - b's list stamps `queue`/b, and its `said` reads "You".
   - a's list stamps `queue`/b, and its `said` reads "Ben".
   - Both lists' `saidAll` read "Ben".
2. Lane 2 asks its lead.
   - a reads "Lane 2 waits on the lead".
   - b reads "Lane 2 waits on Ama's lead".
   - No "you" on either page.
3. One call each: each page leads with its own call and lists the other's under
   the other's label.
4. Unresolved: a checkout with no local seat writes items with no `seat`. Both
   pages read "Someone", and nothing takes over Now.
5. An outrun stamp: the queue moves from b to a after b's pass. b's page reads
   `saidAll` for the drafts item, which no longer takes over Now. a's next pass
   reads "You".
6. A blocker journaled by the supervisor on b, with no lane, is stamped
   `seat:b`.
7. Unshared: an existing unshared fixture's `needs-you.json`, rendered page and
   goldens are byte-identical before and after.
8. Hosted: the delivery holder's published page shows "Ben" and "Ama" by label,
   no chip, "Shared by Ama and Ben", and never "you".

## Execution posture

`test-first` for every story. Each new behaviour has a clear expected outcome,
and the unshared byte-identity is already pinned by existing tests that must
stay green unchanged.

## Sensors

- SEATS-1: `tests.test_waits`, and `tests.test_collab_colead_runtime` with one
  added case in a class that already gives the queue to b
  (`FullHealthAcrossSeatsTests`), so no new fixture is built.
  `tests.test_run_delivery_supervisor` for the re-derived pins.
- SEATS-2: `tests.test_delivery_views_render`, `tests.test_delivery_views_hosted`
  and `tests.test_delivery_views_hosted_projection`.
- SEATS-3: `tests/test_delivery_views_page.py` (the Node page lane, including
  `golden.test.mjs`'s byte-for-byte recapture check).
- Every story: `scripts/validate-registry.py` and
  `scripts/update-provenance.py --check`.
- Before any release that carries this: the full suite with
  `--workers 4 --budget tests/suite-budget.json` on Python 3.11, per
  `AGENTS.md`. Added tests are pure functions or reuse existing fixtures, so
  the budget is kept without raising a ceiling.

## Approval handoffs

1. The operator approves this plan, including decisions 1 to 9.
2. The stories and the four out-of-scope bugs are created with
   `$create-linear-ticket`, under V26-3959's project.
3. Delivery runs under `orchestrate-work`. No release is cut by this plan.

## Compounding

A solution note on who "you" is on a shared record: a word that names an actor
is worded where the actor is known, and checked where the reader is known. A
corpus pin that every `needsYou` `who`/`whom` case has an other-seat form.
