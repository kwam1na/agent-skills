---
reviewSlug: what-everything-waits-on
---

# Show what everything is waiting on, and what will free it

A story, a lane or a whole delivery can sit still for a good reason. To a reader it looks like a gap: nothing on the
live view says whether the thing is forgotten, blocked, or simply next, and the only way to find out is to ask the
lead. Since deliveries on one base can see each other and claim shared files, the reasons something waits have
multiplied. A story can wait on a story the plan orders first, on a lane that holds files it edits, or on another
delivery's claim. A lane can wait on another lane's story. A lead's push can wait on another lead's check or release
on the shared base. A story in review can wait on a round that nothing shows is running.

This plan gives every one of those cases one answer, worked out from the same facts and said in one plain sentence,
which goes away by itself when the wait ends.

Checked against `main` at `35748929` on 2026-11-17, which has `v3.0.0` applied. The survey this plan rests on was
taken there:

- No view says what a story waits on. A planned story's stop reads "Not started" (`STAGE_WORDS` in
  `skills/delivery-views/assets/line-map.js`), and its detail panel has no "waits on".
- The plan's `after` keys reach the delivery file but never the record. A planned entry is
  `{subject, title, lane, stage}` (`planned_entry` in `run_delivery.py`). A story's `Files:` reach only
  `lead-hints`.
- The only code that works out "blocked by" is `next_for_lane` in `delivery_state.py`. It runs for `next` alone and
  needs a tracker snapshot.
- Lane states are `idle, assigned, working, reviewing, handed, blocked, done`, and there is no waiting state. The
  views turn `waitingOn: "lead"` into "Needs you", and `wait` wakes on a newly blocked lane. A planned wait is
  therefore raised as the operator's blocker.
- Cross-delivery holds go through the per-base merge slot: a holder file and one waiter file per waiting process.
  Peer claims live in each record's `extensions.claims`. Both are read today only by the doctor's `peers` step and by
  the warnings `lead-assign` and `lead-accept` print.
- `review-prepare` journals nothing. A lens's return is only a result file on disk. The review card is built from
  reduced and captured rounds alone, so a round in flight never shows.

## What a reader should be able to do

- **On any still thing:** see what it waits on and what will free it, in one sentence, where the thing is shown.
- **On the thing that holds others:** see who is waiting on it.
- **On "Needs you":** see a wait only when someone has to act to end it.
- **On a story in review:** see that a round is running, at which head, since when, and which lenses are back.

## Design direction

The look is the live page's own, held to the same rules as the delivery-story chapter: subtle, and nothing without a
reason.

- A wait is a line of text. It is not a badge, a colour or a new surface. It sits under the thing that waits, in the
  page's secondary text.
- A waiting stop is drawn as progress, the way a later stop is, never in the colour of a problem.
- The thing a wait names is linked: a story to its stop, a lane to its row, another delivery to its live view when
  its address is known.
- Nothing moves because something is waiting. The one new motion is V26-3555's bead for a round in flight, on the
  page's single tempo, and none under reduced motion.

## Finish line

- Both plan lenses align.
- Every case in "The cases" below is shown on the live view, the Markdown view and the built-in page in its one
  sentence, from the derivation's output. No view words a wait itself.
- A wait clears with no action from anyone when what frees it happens, and each case has a test that proves it.
- "Needs you" shows a wait only by Decision 12.
- A round in review is shown from its start to its reduction, by V26-3555's criteria.
- Every story has landed on `main` through the merge queue. The full local suite has passed on macOS with Python 3.11
  at the base head after the last landing, run with `--workers 4 --budget tests/suite-budget.json`, inside the
  budget.
- The operator has been shown the waits and the running round on a real record before the delivery closes.
- Tagging a release is not this delivery's. The change is additive, a minor version, and goes out at the next batch
  boundary.

## Decisions

The operator approves these with the plan.

The shape:

1. **A wait is derived, not stored.** One function, `waits` in `delivery_state.py`, reads the record and returns
   every wait it holds, each as `{subject, on, cause, stage, frees, since?, needsSomeone, words}`:
   - `subject` is what waits: a story key, a lane id, or `lead`.
   - `on` is what it waits on: a story key, a lane id, `{delivery, subject?}` for another delivery, or `lead`.
   - `cause` is one of `order`, `files`, `lane`, `claim`, `slot`, `review` or `held`.
   - `stage` is where the thing it waits on stands, in the queue's and the record's own terms.
   - `frees` names the event that ends the wait: `landed`, `released`, `ended` or `assigned`.
   - `since` is an instant when the record knows one. It is never a duration measured to now.
   - `needsSomeone` is set by Decision 12.
   - `words` is the one sentence.
   The function reads no clock and no network. The only files it reads are the record it is given and the template
   table of Decision 4. The same record and table always give the same waits. A scenario proves it: with the clock's
   functions, the network and every file open other than those two replaced by ones that raise, `waits` still runs.
2. **The record carries what the derivation needs.** A planned entry gains two optional members: `after`, the keys
   the plan orders before it, and `files`, the paths its plan section names. Both come from the delivery file and the
   plan, and `lead-init`, `lead-brief`, the reconcile sweep and `lead-assign` fill them wherever a planned entry is
   entered or found without them. A story without them gets no order or files wait. Absent members are not an error,
   so a record written by an older release reads unchanged. The scenarios prove the writers, not only the
   derivation: each of the four is run on a real plan and delivery file, and the waits are derived from the record it
   wrote.
2a. **What counts as holding a file.** A story waits on files when a file its `files` names is in another lane's
   whole-file grant (`laneOwnership`), and that lane has an unlanded story whose own `files` name the same file. The
   wait names that story. Nothing else gives a `files` wait:
   - a grant to the story's own lane;
   - a grant on paths the story does not name;
   - a grant whose lane has no unlanded story naming the file, as when the work has landed and the grant has not yet
     been revoked;
   - a region grant (`laneRegions`), because lanes share a region-granted file by design.
3. **Cross-delivery facts come from the supervisor.** Another delivery's claims and stories, and the merge slot's
   holder and waiters, are outside the record. Each supervisor pass reads them and writes `peer-waits.json` beside
   the record, as `delivery-peer-waits/1` with `staleAfter`, the way it writes `needs-you.json`. It holds this
   delivery's waits on others and others' waits on this one, in the same shape, worded from the same table. A
   renderer adds them to the record's own waits while the file is fresh. With no file, or a stale one, it adds
   nothing and says nothing about other deliveries.
3a. **The merge slot says what it is held for.** `merge_slot` takes what its caller is doing (`doing`: `push`,
   `sync`, `upgrade`, `check`, `merge` or `release`, plus the subject when there is one), and writes it into both the
   holder file and the waiter file. Every caller passes it: `lead-sync`, `lead-upgrade-writer` and `lead-push` in
   `run_delivery.py`, and the runner in `merge_queue.py`. A holder or waiter file without `doing`, written by an older
   release, reads as "is using the main line", with no subject.
4. **One sentence, made in one place.** `words` is filled by `waits` from one table of templates in
   `skills/orchestrate-work/references/wait-words.json`, in the registry's template forms (a short commit, a lane's
   name, a ticket, a list). The table holds every sentence this plan names: each case's, the holder side's, and the
   two "Needs you" forms. Every view prints `words` and builds no sentence of its own. A test points the table's path
   at a copy in which every template carries its own marker. Each sentence on each of the three views must then carry
   its own template's marker, and no view may show a wait sentence without one.
5. **`next` gives the same answer.** `next_for_lane` takes its order and files reasons from `waits`, and keeps
   adding the tracker's `blockedBy` when a snapshot exists. A story `next` calls blocked is a story the views show
   as waiting, for the same reason.

The cases. Each is one `cause`, and the sentence is the template's. The templates' exact words are settled in
V26-3552 and read by the lead at hand-off; these show the shape.

| Case | Subject | `cause` | Freed when | Sentence, for example |
| --- | --- | --- | --- | --- |
| A story the plan orders after another that hasn't landed | story | `order` | that story lands or settles | Waits for V26-3537 to land. It is in review, round 4. |
| A planned story whose files another lane of this delivery holds for unlanded work | story | `files` | that work lands, or the lead moves the files | Waits for lane 2 to finish V26-3537, which holds `render.py`. |
| A lane in the new `waiting` state | lane | `lane` | the story it names lands | Lane 2 waits for V26-3345 to land before it finishes V26-3346. |
| A lane whose next story waits | lane | the story's cause | the story's wait ends | Lane 1 is next on V26-3538, which waits for V26-3537 to land. |
| A planned story whose files another live delivery on the base claims | story | `claim` | the claim is released, or that delivery closes | Waits for delivery V26-3600, whose V26-3601 claims `render.py`. |
| The lead's push, sync or writer upgrade, or the queue's front piece, while another delivery holds the merge slot | `lead` or the piece | `slot` | the holder releases the slot | The lead's push waits for delivery V26-3600's check of V26-3602, since 2:10 pm. |
| A story in review while a round runs | story | `review` | the round is reduced, restarted or abandoned | In review: round 4 under way at `a1b2c3d`, 1 of 2 lenses back. |
| A story the lead holds for a reason of its own | story | `held` | the story is assigned, or the lead clears it | Held by the lead: waits for the operator's call on the budget. |

When a story waits for more than one reason, it shows each, in the table's order. A lead's reason never hides a
derived one.

The lead's own reason:

6. **A new command and a new kind.** `lead-hold <key> --text T` journals `story.held` with `data.text` (prose),
   subject the story. `lead-hold <key> --clear` journals `story.unheld`, which answers it. A hold ends by itself
   when the story is assigned. Its wait then derives as ended, and nothing more is journalled. A hold is refused for
   a story that has landed, settled or is not in the record. An empty or space-only text is refused.

The lane's side:

7. **A lane state `waiting`.** A lane row may be `waiting`, and then its `waitingOn` must be a subject the record
   holds. The lane sets it through `focus --state waiting --waiting-on KEY`. Anything else is refused, naming the
   field. A `waiting` state with no `--waiting-on` is refused too. Setting `waiting` keeps the row's prior state in a
   new tool-written member, `waitingFrom: {state, at}`. It is not `restedFrom`, which the tool resets at every lane or
   lead write and rewrites when the runner speaks over a row. `waitingFrom` is carried across every write while the
   state stays `waiting`, whoever writes, and is removed when the row returns. A stop does not end the wait, because
   the subject has not landed. In the same write as each event:
   - On a landing, `lane_rows_after` first returns every `waiting` row whose `waitingOn` is the subject, whichever
     lane it belongs to and whatever its focus names, before any of its early returns. Each returned row takes the
     state in `waitingFrom`, `waitingOn` is cleared, and `waitingFrom` is removed. For the subject's own lane this
     takes the place of the rest.
   - On a settlement, the no-change hand-off and `accept_handoff` each do the same return, before `_rest_if_free`.
   - On a stop, only the subject's own lane's row returns, if it is `waiting` on the subject. It takes its
     `waitingFrom` state, and then the stop's rest applies to it with `waitingOn` "lead", before any early return,
     whatever its focus names. `lane_rows_put_back` then treats it as today. A `waiting` row in another lane stays
     `waiting`, with the same `waitingOn` and `waitingFrom`, and is counted among what waits on the stopped piece.
   The `wait` event for a landing or a settlement names every lane it freed. Any other write that takes a row out of
   `waiting`, such as the lane's own `focus` to another state or a hand-off, removes `waitingFrom` and clears a
   `waitingOn` that named the waited-on subject.
8. **`wait` does not wake on a planned wait.** A `waiting` row is not a blocked one, and `wait` neither wakes on it
   nor counts it as quiet. A `waiting` row is not in "Needs you" and does not hold the close.
9. **`lead-accept` settles the row.** After an acceptance, the accepted lane's row is `idle` with `waitingOn` naming
   the accepted key. It clears to `null` when that key lands, as in Decision 7. The lead patches nothing after an
   acceptance.
10. **The brief tells the lane.** The lane brief says to use `waiting` for "waits for KEY to land", and `blocked`
    only for a question someone has to answer.

Where it is said:

11. **On the thing that waits, everywhere it is shown.**
    - On the live page: a waiting stop's line in "What's left", its detail panel under its stage, the stop's
      accessible label, and the lane's row.
    - On the Markdown view and the built-in page: under the story and under the lane.
    - On the thing that holds: a line naming who waits on it, under its detail panel and its row. For another
      delivery: "V26-3700's V26-3701 waits on this delivery's claim on `render.py`."
    - On the tracker: only a lead's hold, as `story.held` posting to the story's ticket under "Waiting", and its
      clearing by `lead-hold --clear`. A hold ended by assignment posts nothing of its own, because the assignment's
      "Started" post follows it. Derived waits post nothing, because they change with every pass.
12. **"Needs you" shows a wait only when someone must act.** There is no clock. A wait is progress while what it
    waits on is moving. It sets `needsSomeone` in two cases, and only these reach "Needs you", shown and never
    holding the close:
    - What frees it needs someone. The thing waited on is stopped, blocked on the lead, or in "Needs you" itself. In
      this case the existing item for that thing gains the count of what waits on it, for example "V26-3537 was
      stopped. 2 pieces wait on it.", and no second item is added.
    - Nothing holds the story any more. It is planned, every wait has ended, it is not held, and its lane is idle.
      The item reads "V26-3538 is free to start. Lane 1 is idle." A lane counts as idle in its `idle` state, including
      one Decision 9 left waiting on its accepted story's merge.
    A wait on something that is moving, such as a story in review, checking or queued, has `needsSomeone` false.
13. **The story chapter.** A derived wait is present tense and is neither a thread nor a beat: it is not in the
    narrator's story. A lead's `story.held` is a beat at the story altitude with its text, and `story.unheld` is
    bookkeeping. V26-3555's round start is a beat at the story altitude, and a lens's return is bookkeeping.

The review round:

14. **Two kinds for a round in flight.**
    - `review.started` is journalled by `review-prepare` and carries `{round, head, lenses, restart}` and an
      optional prose `why`, which the lane gives with `--why`.
    - `review.lens.returned` is journalled once per lens result that passes the brief's identity check, with
      `{round, lens, verdict, findings}`. The supervisor's pass checks for new results of a round in flight, and
      `review-reduce` journals any it finds unjournalled before it records the round.
    - `review.round` answers its `review.started`.
    - A round is running from its latest `review.started` until a `review.round` for it, a later `review.started`
      for the story, or the story leaving review: handed, settled or reassigned.
    - A lens result is journalled at most once, whichever of the pass and `review-reduce` sees it first. `restart` is
      true on a start that `--restart` wrote, and false otherwise.
    - The `review` cause in `waits` is derived from these entries, and the review card's headline is that wait's
      `words`.
15. **A lens saying what it is doing mid-round is out of scope.** Nothing writes it today, and this delivery adds no
    channel for it. The card shows "reading" and how long the round has run.

The delivery:

16. It lands on `main`, story by story, through the merge queue, with the base's usual `focused` posture and the
    full suite once at the end.
17. New kinds are added the way the guide says: the registry, its vectors, the writer inventory in
    `tests/test_journal_shape.py`, and the tracker's kind list. Each story that adds a kind re-derives those pins.
18. No story starts a process its tests did not already start: `waits` is pure and tested in-process. Page cases
    reuse the existing page fixtures. The last recorded headroom was 38 seconds of 1,740.

## Out of scope

- A wait measured against a clock, or "overdue".
- A lens reporting progress mid-round.
- Storing waits in the record as state.
- Changing how `next` ranks work, beyond taking its reasons from `waits`.
- Refusing work because of another delivery's claim. Claims stay a warning.
- Hosts other than the two the release projects to, and adopter-specific overlays.
- Tagging a release, and applying it here.

## Approval Handoffs

- The operator has approved delivering this plan once its lenses align, on `main`, with two lanes, each granted
  push and draft change requests, and the lead granted ready change requests.
- The lead reads the wait templates as V26-3552 hands them off, and accepts them or sends them back.
- The operator sees the waits and the running round on a real record before the delivery closes.
- Raising a number in `tests/suite-budget.json` is the operator's alone.

## Execution posture and sensors

Test-first throughout. Each case's wait, and its clearing, is written as a failing test against a fixture record
before the code that makes it pass. The page cases come before the page code. The one characterization is V26-3426's
existing lane-row and `wait` behaviour for `blocked`, pinned before `waiting` is added beside it.

Sensors, as `AGENTS.md` names them:

- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`, on every story. `wait-words.json` is a
  new shipped file. V26-3552 adds it to `provenance.lock.json` by hand and refreshes the digest with the writer.
- Each story's own test modules, through `scripts/run-tests.py`.
- `tests.test_orchestration_state` on every story that edits the orchestration guide or a skill page.
- `tests.test_journal_kinds` and `tests.test_journal_shape` on every story that adds a kind.
- `tests.test_delivery_views_page` on every story that changes the page.
- The full suite once, at the base head after the last landing, inside `tests/suite-budget.json`.

Shared resources: none new. No story adds a port, a fixed path or a serial unit. V26-3554's cases build on
`PeerDeliveryTests`' existing two-delivery fixture, which is already `run_quietly`, and add no class that runs alone.

Pins that go stale, and the story that re-derives them:

- The registry's kind list in `tests/test_journal_kinds.py`, its vectors, the inventory in
  `tests/test_journal_shape.py`, and `ALL_KINDS` in `tests/test_run_delivery_tracker.py`: V26-3552 for `story.held`
  and `story.unheld`, and V26-3555 for its two kinds.
- The lane states and the lane row's members in `schemas.json`, with the new `waitingFrom`, and wherever
  `tests/test_orchestration_state.py` and `tests/test_lane_rows.py` list them: V26-3426.
- The planned entry's members: V26-3552.
- The merge slot's holder and waiter file members, wherever `tests/test_run_delivery_peers.py` and
  `tests/test_merge_queue.py` assert them: V26-3554.
- Goldens under `tests/fixtures/delivery-views/` that show a planned stop, a lane row or a review card: the story
  that changes what they show, each change named in its hand-off.

## Scenarios

Each row names the freeing event or refusal it proves. A refusal leaves the record unchanged and names what is wrong.

For the writers of `after` and `files`, on a real plan and delivery file, in V26-3552:

- `lead-init`, then `lead-brief`, on a plan with a story ordered after two keys and a `Files:` sentence give that
  planned entry exactly those `after` keys and those `files`.
- On a record whose planned entry lacks both members, the reconcile sweep fills them, and so does `lead-assign`.
- From the record `lead-init` wrote, with neither `after` key landed, `waits` gives the story one `order` wait for
  each key. A story with neither member gives no order or files wait, and no error.

For `waits`, from fixture records, in-process, in V26-3552:

- An `order` wait on a story in review names that story and its round, and has `needsSomeone` false. When that
  story merges, the wait ends. When it settles instead, the wait ends too.
- With two `after` keys, landing only the first leaves one `order` wait, on the second.
- A `files` wait, by Decision 2a: lane 2's whole-file grant on a file the story names, with lane 2's unlanded story
  naming it, gives a wait on that story. When that story merges, the wait ends. When the lead moves the file to the
  story's own lane instead, it ends too.
- No `files` wait arises in any of these cases: lane 2's grant covers only paths the story does not name; the grant
  is to the story's own lane; lane 2's story naming the file has merged and the grant still stands; or lane 2 holds
  the file only by a region grant.
- A story that waits for both reasons gives both, in the table's order.
- `lead-hold` gives a `held` wait with the lead's text, and journals `story.held`. Assigning the story ends the wait
  and journals nothing more. `lead-hold --clear` journals `story.unheld`, which answers the hold.
- A held story with an unlanded `after` key gives both waits, in the table's order.
- `lead-hold` is refused, each time with the record unchanged, for: a landed key, a settled key, a key not in the
  record, `--text ''` and `--text '  '`.
- `story.held` and `story.unheld` each post once to the story's ticket under "Waiting". A supervisor pass that
  derives an `order` wait posts nothing.
- `needsSomeone` is set for a wait whose blocker is stopped, for one whose blocker's lane is blocked on the lead, and
  for one whose blocker is itself in "Needs you". In each case "Needs you" holds exactly one item for the blocker,
  carrying the count of what waits on it. That item does not hold the close.
- "Free to start" applies to a planned story with no wait left, not held, on an idle lane. It is in "Needs you" and
  does not hold the close. It is not in "Needs you" in any of these cases: the story's lane is busy; the story is
  held; or it still has a wait.
- On the idle lane Decision 9 left, still waiting on its accepted key, a planned story with no wait that is not held
  is "free to start" in "Needs you". The same lane with a story that has a wait is not.
- With the clock's functions, the network and every other file open replaced by ones that raise, the same fixture
  gives the same waits twice.
- `next` on a fixture with no tracker snapshot reports a story with only an `order` wait, and a story with only a
  `files` wait, as blocked, for the same `cause` and `on` that `waits` returns. It reports a story with no wait as not
  blocked. With a snapshot, it still adds the tracker's `blockedBy`.

For `waiting` lanes and acceptance, in V26-3426:

- Characterization first: a `blocked` lane wakes `wait`, is in "Needs you", holds the close and is counted as quiet
  when unmoved. All four stay true afterwards.
- `focus --state waiting --waiting-on KEY` for a known key writes the row. With an unknown key it is refused, naming
  the field, and with no `--waiting-on` it is refused too.
- A `waiting` lane does not wake `wait`, is not counted as quiet, is not in "Needs you" and does not hold the close.
- A lane that was `working` returns to `working` when its subject merges, in the same write. A lane that was
  `idle` returns to `idle`.
- A lane that was `working` sets `waiting`. Before its subject merges, it says `focus` once more, the runner then
  speaks over its row, and the lead then writes it, each changing its focus. `waitingFrom` is still `{state:
  working}` after each write. When the subject merges, the row returns to `working` with `waitingOn` null and no
  `waitingFrom`, and `lane_moved_off` reads the returned state.
- Lane 3, `working`, waits on lane 2's story. It returns to `working`, with no `waitingFrom`, in each of three
  cases: when that story merges, when it settles through a no-change hand-off, and when it is settled through
  `accept_handoff`. In each settle case the settlement's `wait` event names lane 3. A waiting row whose last focus
  does not name the subject returns as well.
- One landing frees lane 3 and lane 1, both waiting on it. Both return, and the landing's `wait` event names both.
- Lane 3, `working`, waits on lane 2's story, which the queue then stops. Lane 3 stays `waiting` with the same
  `waitingOn` and `waitingFrom`, and is counted in the stopped item's "N pieces wait on it". When the story is put
  back and merges, lane 3 returns to `working`, and the `wait` event names it.
- A lane that was `working` sets `waiting` on its own handed story, which the queue then stops. The row is
  `working`, with `waitingOn` "lead" and no `waitingFrom`. The same holds when the row's last focus does not name
  the stopped story. When the story is put back, the row is `handed`, as today.
- A lane that was `working` sets `waiting` on its own story and holds no other open work. The story then settles
  through a no-change hand-off, and the row ends `idle`, with `waitingOn` null and no `waitingFrom`. The same holds
  when the story is settled through `accept_handoff`.
- A lane `waiting` on lane 2's story hands its own story with no change. The row is `handed`, with no `waitingFrom`
  and a `waitingOn` that no longer names lane 2's story. A lane `waiting` that says `focus --state working` likewise
  keeps no `waitingFrom`.
- An idle lane whose next planned story waits shows that story's wait under the lane.
- After `lead-accept`, the lane is `idle`, waiting on the accepted key, and clears to `null` when that key merges.

For the review round, in V26-3555:

- `review-prepare` journals `review.started` with its round, head, lenses, `restart: false`, and the lane's `--why`,
  or no `why` when none was given. A `--restart` journals a new start with `restart: true`.
- Two lens results, one written before a supervisor pass and one after it, then a second pass, then
  `review-reduce`, give exactly one `review.lens.returned` per lens. A result that fails the identity check is never
  journalled. `review-reduce` then journals the round, which answers the start.
- `waits` gives a story with a `review.started` and one `review.lens.returned` of two lenses one `review` wait, with
  the round, the head and "1 of 2 back". The card's headline equals that wait's `words`.
- After a restart's new start, the old start no longer gives a running round. None is running either after the
  story is handed with a round never reduced, after it is settled, or after it is reassigned.

For other deliveries, on `PeerDeliveryTests`' existing two-delivery fixture, in V26-3554:

- A planned story whose file the peer claims gives a `claim` wait naming the peer and its story, in this delivery's
  `peer-waits.json`. The peer's file names this delivery as waiting on its claim. Releasing the claim clears both on
  the next pass. So does the peer delivery closing while its claim is still listed.
- With the peer holding the slot for a check of a named piece, this delivery's `lead-push` gives a `slot` wait
  naming the peer, "check of" that piece, and since when. The same holds for `lead-sync` and for
  `lead-upgrade-writer`. The runner's front piece, waiting on the slot, gives a `slot` wait for the piece. The
  holder's file names each waiter and what it is doing, and both sides clear when the slot is released.
- A holder file without `doing`, and a waiter file without it, each read as "is using the main line".
- A fresh `peer-waits.json` shows the `claim` sentence, the `slot` sentence and the holder side's line on the page,
  the Markdown view and the built-in page. The same file past `staleAfter`, and no file at all, each show nothing
  about other deliveries.

For the views, in each story for its own cases:

- Each case's sentence appears where Decision 11 says, on the page, the Markdown view and the built-in page. It is
  gone after the event that frees it, with no reload on the page.
- The marker test of Decision 4 passes for every template the story adds. V26-3552 adds the test and covers its own
  templates. Each later story extends it to its own.
- While a round runs, the review card shows "Round N under way", the head, how long ago it started, a chip of
  lenses back, the lane's reason when given, one row per lens, and earlier notes folded. It settles to today's card
  when the round is reduced.
- Under reduced motion nothing moves.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3552, V26-3426 | `skills/orchestrate-work/references/wait-words.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `skills/run-delivery/templates/lane-brief.md`, `tests/test_waits.py`, `tests/fixtures/waits/`, `tests/test_lane_rows.py`, `tests/test_lead_wait.py`, `tests/test_orchestration_state.py` |
| 1 | 2 | V26-3555 | `skills/delivery-views/scripts/review_projection.py`, `skills/orchestrate-work/scripts/narrator.py`, `tests/test_waits_review.py`, `tests/test_delivery_views_review_projection.py`, `tests/test_narrator.py`, `tests/fixtures/narrator/` |
| 2 | 2 | V26-3554 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py`, `tests/test_run_delivery_peers.py` |

Shared by region, granted by the lead with `lead-own --region` when each story is assigned and revoked when it
lands:

- `skills/orchestrate-work/scripts/delivery_state.py` and `skills/orchestrate-work/scripts/render_state.py`
- `skills/orchestrate-work/references/schemas.json`
- `skills/run-delivery/scripts/run_delivery.py`
- `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css` and
  `skills/delivery-views/scripts/render.py`
- `skills/orchestrate-work/references/journal-kinds.json`, `tests/fixtures/journal-kinds/vectors.json`,
  `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/test_run_delivery_tracker.py`
- `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py` and `tests/test_orchestration_render.py`
- `tests/test_delivery_views_page.py`, `tests/test_delivery_views_render.py` and the goldens under
  `tests/fixtures/delivery-views/`

`wait-words.json` is created by V26-3552, so lane 1 claims it whole. When V26-3552 lands, the lead revokes that
claim and grants each later story a region of it, so each story adds its templates in a region of its own. A story's new page test file under `tests/delivery_views_page/` is a whole-file claim of its lane.
`provenance.lock.json` is regenerable: each story takes the lock side that lists its new files and runs the writer,
as `AGENTS.md` describes.

Lane 1 and lane 2 start at once. V26-3552 lays down the shape, the record's new members, the derivation and the
table, and V26-3426 and V26-3554 build on it. V26-3555 builds its kinds, their journalling and the card at once,
reading the journal. Its `review` cause in `waits`, the card's headline from `words`, and its lines on the Markdown
view and the built-in page need `waits`. So V26-3555 hands off only after V26-3552 has landed: the lead authorizes a
base merge with `lead-authorize-base-merge`, and the lane merges and finishes on that base. When V26-3552 has landed,
lane 1 goes on to V26-3426. Lane 2 goes on to V26-3554 when both V26-3552 and V26-3555 have landed.

## Stories

### V26-3552. Say what a story that hasn't started is waiting on

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/wait-words.json`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/delivery-views/assets/line-map.js`, `skills/delivery-views/scripts/render.py`, `tests/test_waits.py`,
`tests/fixtures/waits/`, `tests/test_orchestration_state.py`, `tests/test_orchestration_render.py`,
`tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/test_run_delivery_tracker.py`,
`tests/test_run_delivery.py`, `tests/test_delivery_views_page.py` and `tests/fixtures/delivery-views/`.

Write `waits` by Decisions 1, 2a, 4 and 5 for the `order`, `files` and `held` cases, with the marker test. Write
`wait-words.json` with this story's templates and the two "Needs you" forms. Give planned entries `after` and
`files` by Decision 2, filled by `lead-init`, `lead-brief`, the sweep and `lead-assign`. Add `lead-hold`,
`story.held` and `story.unheld` by Decision 6. Set `needsSomeone` and add the two "Needs you" forms by Decision 12.
Show the sentence on the page, the Markdown view and the built-in page by Decision 11. `next` takes its reasons from
`waits`.

Acceptance criteria:

- `c1` A planned story whose `after` keys have not all landed says which story it waits on and where that story
  stands.
- `c2` A planned story whose files another lane holds says which lane and for which story.
- `c3` The lead can record why a story waits, and that reason is shown with the story.
- `c4` An idle lane whose next story is waiting is shown as waiting, with the same reason, not as stalled.
- `c5` When the wait ends, the reason goes away with no action from the lead.

### V26-3426. Give a lane a "waiting on another story" state that the views show as progress, not as the operator's blocker

Lane 1, tier 2, after V26-3552. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/references/wait-words.json`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/templates/lane-brief.md`, `skills/delivery-views/assets/line-map.js`,
`skills/orchestrate-work/scripts/render_state.py`, `tests/test_lane_rows.py`, `tests/test_lead_wait.py`,
`tests/test_orchestration_state.py`, `tests/test_run_delivery.py` and `tests/test_run_delivery_supervisor.py`.

Pin today's `blocked` behaviour first. Then add `waiting` by Decisions 7 and 8, make `lead-accept` settle the row by
Decision 9, and change the brief by Decision 10. The lane's `lane` wait comes from `waits`, with its templates added
to the table and to the marker test.

Acceptance criteria:

- `c1` A lane waiting on another story's merge isn't in "Needs you" and doesn't hold the close, and the row clears
  when that story merges.
- `c2` After `lead-accept`, the lane's row is no longer `handed`/`blocked`, waiting on the lead.

### V26-3555. Show a review round while it is running

Lane 2, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/references/journal-kinds.json`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/render_state.py`,
`skills/orchestrate-work/references/wait-words.json`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/delivery-views/scripts/review_projection.py`, `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css`,
`skills/orchestrate-work/scripts/narrator.py`, `tests/test_waits_review.py`,
`tests/test_delivery_views_review_projection.py`, `tests/test_narrator.py`, `tests/fixtures/narrator/`,
`tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/test_run_delivery_tracker.py`,
`tests/test_run_delivery.py`, `tests/test_orchestration_render.py` and `tests/test_delivery_views_page.py`.

Add `review.started` and `review.lens.returned` by Decision 14, journalled by `review-prepare`, the supervisor's pass
and `review-reduce`. The narrator reads them by Decision 13. The review card follows the direction on the ticket.
After the authorized base merge that brings V26-3552 in, add the `review` cause to `waits` with its template. Then
make the card's headline that wait's `words`, and show it on the Markdown view and the built-in page.

Acceptance criteria:

- `c1` While a round runs, the story's review shows that round as under way, at the head being reviewed, with how
  long ago it started.
- `c2` The last finished round's outcome is shown with its candidate and how many commits the branch has moved since.
- `c3` Each lens of the running round shows as back, with its verdict, or still reading; a lens that returns updates
  in place with no reload.
- `c4` The lane's reason for the round is shown when it gave one, and nothing is shown when it did not.
- `c5` Earlier rounds' confirmed notes are folded while a round runs and open as they do today.
- `c6` When the round is reduced, the card settles to the finished round's card as it is today.
- `c7` A round restarted or abandoned does not leave a round shown as running.
- `c8` Nothing moves under reduced motion, and every looping motion keeps the page's shared tempo.

### V26-3554. Show a wait on another delivery sharing the base, in both deliveries

Lane 2, tier 2, after V26-3552 and V26-3555. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/references/wait-words.json`, `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/assets/line-map.js`, `skills/orchestrate-work/scripts/render_state.py`,
`skills/orchestrate-work/references/schemas.json`, `tests/test_run_delivery_peers.py`, `tests/test_merge_queue.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_delivery_views_render.py`, `tests/test_orchestration_render.py` and `tests/test_delivery_views_page.py`.

Make the merge slot say what it is held for, by Decision 3a, in `merge_slot` and every caller. Write
`peer-waits.json` by Decision 3 for the `claim` and `slot` cases, on both sides, from the peers registry, each peer's
claims and planned files, and the slot's holder and waiter files. The renderers add it while fresh. The templates go
in the table and the marker test.

Acceptance criteria:

- `c1` A story or lane that needs a shared file another delivery claims says which delivery holds it and for which
  story.
- `c2` A lead whose acceptance or push is held by another lead's check or tag on the shared base sees which delivery,
  what it is doing, and since when.
- `c3` The holding delivery shows which deliveries are waiting on its claim or check.
- `c4` Both sides read it from the same facts, in the shape the epic settles for "waits on".
- `c5` When the claim is released or the check ends, both sides clear with no action from either lead.

### V26-3565. Catch a provenance listing change at hand-off, not in the merge queue

Lane 1, tier 3, after V26-3426. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/scripts/merge_queue.py`, `skills/run-delivery/templates/lane-brief.md`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/adopting.md`, `tests/test_run_delivery.py` and
`tests/test_merge_queue.py`.

Added mid-delivery. The merge queue stopped V26-3552 after its acceptance, because the story added a shipped file to
`provenance.lock.json`'s listing and its lane didn't own the lock (decision 131). Nothing caught it earlier. The
operator asked to add the fix to this delivery.

`handoff` runs the queue's listing check (`check_shared_outputs` and `check_listing`) before it records anything. It
refuses a candidate that changes a regenerable listing its lane doesn't own, with the queue's guidance: the file, the
outputs added or removed, and the grant to ask the lead for. It reuses the queue's code rather than copying it. A
digest-only refresh stays free for any lane. The lane brief states the rule once. It names each regenerable listing
from the binding's `regenerable.listings`, never this repository's `provenance.lock.json` by name, and says nothing
when the binding declares none.

The story stays independent of this repository's layout. `provenance.lock.json` is only the case that exposed the
gap. A `lead-assign` warning was considered and left out: no binding says which new files a listing will take on, so
the warning would either hard-code this repository's rule or warn on every new file.

### V26-3566. Let lead-plan drop one criterion, and refuse a flag a change doesn't use

Lane 1, tier 4, after V26-3565. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/delivery-views/assets/line-map.js`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery.py` and
`tests/delivery_views_page/line-map-plan.test.mjs`.

Added mid-delivery. Plan change c13 removed V26-3565 when the lead meant to drop one of its criteria:
`lead-plan change --kind removed` removes whole stories and silently ignores `--criterion`, and no kind drops a
single criterion. The operator asked to add the fix to this delivery.

`lead-plan change` refuses any flag its kind doesn't use, naming the flag, before it writes anything. `--kind removed`
with `--criterion` drops that one criterion as a recorded change with its reason. It refuses a criterion the story
lacks, and the story's last criterion. The plan chapter strikes a dropped criterion through and shows its reason, as
it does for a removed story.

### V26-3567. Show what needs someone as one list, in the lanes' own visual language

Lane 1, tier 3, after V26-3426. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/orchestrate-work/references/wait-words.json`,
`skills/run-delivery/scripts/run_delivery.py`, `tests/delivery_views_page/line-map-needs-you.test.mjs` and
`tests/test_run_delivery_supervisor.py`.

Added mid-delivery. The operator approved a live mock of "Needs you" in the lanes' visual language, merged with
"Needs attention", and asked to add it to this delivery.

"Needs attention" goes, and its blocked-lane items become rows of one list. The page's reader is the operator, but
many items need the lead, so the list names who must act rather than saying "you". Its heading is neutral. It opens
with one sentence counting the items by who must act and how many hold up the finish, for example "1 thing needs the
lead, and it's holding up the finish.", replacing the "Holds the close" and "Shown only" headers. Each row's meta
line opens with who acts: **You** for the operator, **The lead**, or a lane by name. Rows that need the operator come
first. On the page, "you" always means the reader, and the lead is always named. Each row has:
- a faint stroke of its lane's line, with the story's stop as the line chart draws it and its short key beneath;
- for a wait, the blocker's stop in its own lane's colour, joined by a hairline;
- a short headline, one softer line of why, and a meta line with "Holding up the finish" for holding items and what
  clears it;
- "for <age>" in a right-aligned column, with the clock time on hover.

Holding rows come first at full weight. Red appears only on a stopped stop. Each "Needs you" item carries its facts
(lane, subject, state, blocker, since, holding), and its words come from `wait-words.json`, so the page words
nothing itself.

### V26-3568. Draw a setback's thread by its state, and fold long check lists in the story

Lane 2, tier 3, after V26-3554. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/orchestrate-work/references/journal-kinds.json`,
`tests/fixtures/journal-kinds/vectors.json` and `tests/delivery_views_page/line-map-threads.test.mjs`.

Added mid-delivery. The operator approved a live mock of setback threads drawn by their state, and asked to include
folding long check lists.

A thread's margin line takes the story thread's state: the held colour while open, and the page's neutral hairline
once closed. The row with the thread's opening entry carries a small ring on the line, and the row with its closing
entry a short end tick, so adjacent setbacks read as separate. A beat whose line names checks, such as
`handoff.checked` and `check.selected`, says their count and why they were chosen in its line, and lists the names in
its fold. Its words change in the registry and its vectors, not in the page. Every mark is drawn from the story's
existing thread facts.

It also fixes the Checking card's finishing animation, folded in at the operator's request. Each record write
rebuilds the card from scratch ([`line-map.js`](skills/delivery-views/assets/line-map.js), around the running row), so a
finishing check's hand-off is cut short: the arc filling, the tick or cross, the hold and the turn, about two seconds
in all (`SETTLE_MS`, `HOLD_MS`, `TURN_OUT_MS`, `TURN_IN_MS`). Two-second hosted checks interrupt every one. A redraw
mid-hand-off keeps the animating row and queues the next result. Checks that finish faster than the animation get a
shortened one, so the card never falls behind the record.

## Calls made

The operator settled these on 2026-11-17, before drafting.

1. Waits are derived from the record's facts, plus a reason the lead states. They are not stored as rows (Decisions
   1 to 6).
2. A wait reaches "Needs you" only when what frees it needs someone, or when nothing holds a story whose lane is idle.
   There is no clock (Decision 12).
3. V26-3555 is in this delivery: a story in review waits on its round.
4. The delivery lands on `main` directly. The change is additive, a minor version.

Settled by this plan, for the lenses to test:

5. A derived wait is not part of the narrator's story. Only a lead's hold, and a round's start, are beats (Decision
   13).
6. Only a lead's hold posts to the tracker (Decision 11).
7. Two lanes, with region claims on the shared files, rather than one lane running all four stories in sequence.
8. V26-3555 starts at once and finishes on a base that holds V26-3552, through an authorized base merge, rather
   than starting only after V26-3552 lands.
