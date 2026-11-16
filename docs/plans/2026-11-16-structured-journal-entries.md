---
reviewSlug: structured-journal-entries
---

# Let a delivery tell its own story

A delivery's record is a log. Each entry is `{id, at, kind, lane, subject, summary}`, where `summary` is a sentence a
tool wrote for whoever was debugging it. An engineer or a stakeholder who opens the live page wants something else:
what was delivered, how long it took, what went wrong and how it ended, and what the lead decided and why. Today the
page gets there by matching patterns against those sentences, and over half of what it finds is bookkeeping.

This plan changes what the record keeps and adds the thing that reads it. The record keeps facts and how they
connect. A narrator turns them into a story a person can follow at the depth they choose. It breaks the journal's
shape on purpose and keeps no path for the old one.

Checked against `main` at `7e63d59` on 2026-11-16, which has `linear-v2.4.0` installed. The inventory this rests on
was taken there: three scripts write entries, all through `delivery_state.append_journal`, at about 60 write sites,
in 32 kinds; `decision.recorded` alone has 25 distinct meanings; and twelve places in the tools read their own
sentences back by prefix or pattern.

## What a reader should be able to do

This is the goal every decision below serves.

- **A stakeholder, in a minute:** read what was delivered, how long it took, each setback with what it cost and how
  it ended, and each decision the lead made with its reason. Nothing they read needs the tools' vocabulary.
- **An engineer, following along:** see each piece's journey step by step, and for any step, what caused it and
  what it led to, down to the commit, the check and the log.
- **Either, on any entry:** ask what this kind of event is and why it matters, and get one sentence.

## The design

Four ideas, each new to this record.

1. **Facts, not sentences.** An entry is `{id, at, kind, lane, subject, data}`. `data` holds that kind's facts, and
   its shape is fixed by the kind. No entry stores a sentence. Free text exists only where a person wrote it: a
   lead's decision, a blocker, a reason.
2. **Events know what caused them.** An entry may name the entries it happened `because` of and the entries it
   `answers`. A stop names the failed check. A repair answers the break. A re-queue answers the stop. The journal
   becomes a graph, and a problem becomes a thread with a beginning, a cost and an ending, not five rows scattered
   among a hundred.
3. **Every kind declares how much it matters.** One registry lists each kind with its facts, its altitude
   (`headline`, `story` or `bookkeeping`), its words, and one sentence explaining what the event is. A reader picks
   an altitude; nothing decides relevance by guessing at prose.
4. **A narrator writes the story.** One module reads the journal and the registry and produces a `story/1` document:
   a digest, chapters that each end at a landing, the beats inside them, and the threads. Every view shows that
   document. The page and the Markdown view show it. A tracker post and a `lead-wait` event, which each tell one
   entry, take that entry's words from the registry. None of them words an entry itself.

For this repository's last delivery, the headline altitude of that story would read along these lines. The times
and counts here are illustrative; the story's exact words are settled in JF-2, from a fixture of that record:

> **Six pieces in nine hours.** Two setbacks cost 54m. The lead made four decisions.
>
> **Getting started** · 10:07 am. Three lanes picked up five pieces.
>
> **V26-3425 landed** · 11:50 am, 1h 43m after it was picked up, after 2 review rounds.
>
> **V26-3319 landed** · 1:26 pm, after 2 review rounds.
>
> **The plan grew** · 2:26 pm. The lead added V26-3513, from notes raised on V26-3318.
>
> **A setback: V26-3264 was stopped** · 2:28 pm to 3:09 pm, 41m. A check failed because the guide named a file the
> skill doesn't ship. The lane fixed it in a fifth review round and handed it off again.
>
> **V26-3318 landed** · 2:44 pm, after 1 review round.
>
> **V26-3264 landed** · 3:28 pm, after 5 review rounds.
>
> **V26-3513 landed** · 3:41 pm.
>
> **A setback: the main line broke** · 4:55 pm to 5:08 pm, 13m. The lead's own commit broke a test. The lead reverted
> it. V26-3320 waited.
>
> **V26-3320 landed** · 5:18 pm, after 3 review rounds.
>
> **Finished** · 7:07 pm. Every finish line met. The close-out's full run was skipped by the operator's decision.

The same record at the `story` altitude adds each round, hand-off, acceptance and check run under its chapter. At
`everything` it adds the 190 entries of bookkeeping.

## Design direction

The lead sets the story chapter's look, and holds every story to it: subtle, and nothing without a reason.

- The story is typography first. A chapter is a heading and a time. A landing is a line, not a card. Colour appears
  only where something went wrong or landed, as on the rest of the page.
- A setback is the one thing that gets a surface: a single quiet sheet with its cause, what it held up and how it
  ended, and its cost as the only number in view.
- Altitude changes density, not layout. Switching altitude adds or removes rows in place, on the shared fold's
  motion, so a reader keeps their place and sees what the extra depth added.
- Cause is shown lightly: at the story altitude a thin line in the margin ties a thread's entries together. No
  arrows and no graph.
- An explanation waits to be asked for. It appears when a row is opened, never as standing text.
- Motion only where state changes, on the page's one tempo. A new beat arrives as a new row does. An open setback
  has the page's single slow pulse, and a closed one is still.
- Words stay plain: short sentences in the page's voice, past tense for what happened, no jargon and no
  celebration.

## Finish line

- Both plan lenses align.
- No tool writes or reads a sentence in the journal. `delivery-journal/2` and every registry shape have no `summary`
  member, every call that writes an entry passes a registered kind and no sentence, and every reader of an entry
  gives the same result when the entry's prose fields are replaced by other text.
- Every entry a tool writes validates against its kind's entry in the registry, and carries the links the kinds
  table gives it.
- The narrator produces, from a fixture of this repository's last delivery, a headline story of the shape above that
  the operator has read and approved, and a story for every state the scenarios name. Journals the real writers
  produce narrate to closed threads.
- The live page's "What happened" chapter shows the story at three altitudes, each setback as one thread, and an
  explanation for any entry. The Markdown view and the built-in page print the story. A tracker post and a
  `lead-wait` event carry the registry's words for their entry.
- The work is ready to merge and not merged: every story has landed on the integration branch
  `integrate/delivery-story`, a change request from it into `main` is open, and the full local suite has passed on
  macOS with Python 3.11 at that candidate, run with `--workers 4 --budget tests/suite-budget.json`, inside the
  budget.
- The operator has been shown the new design running: the live page narrating a real record at each altitude.
  Merging into `main`, and everything after it, waits for the operator.

## Decisions

The operator approves these with the plan.

The record:

1. The journal becomes `delivery-journal/2`. An entry's members are `id`, `at`, `kind`, `lane`, `subject`, `data`,
   and optionally `because`, `answers` and `occurredAt`. `summary` is removed. `review`, `recovery`, `reviewCarry`,
   `reviewArtifact` and `baseMergeAuthorization` move inside `data` of the kinds that carry them.
2. No path reads `delivery-journal/1`. An entry carries no version, so the old shape is known by what it holds: an
   entry with a `summary`, or without `data`, is refused, and the refusal names the release that reads it. A record
   whose journal is empty is valid in either shape. A delivery open under an older release closes there before the
   new one is applied. The refusal arrives with JF-7, when the bridge of Decision 25 goes.
3. `decision.recorded` stops being a catch-all. What a tool does gets its own kind, listed below.
   `decision.recorded` and `blocker.recorded` remain for what a person decides or reports. Both require
   `data.text`, a decision also requires `data.why`, and neither may be empty or only spaces.
4. A field is either a typed fact or prose, and the registry says which. The prose fields are `text`, `why`,
   `detail`, `note`, `notes` and `title`; nothing reads their content to decide anything. Every other field is
   typed and checked: `head` and every other commit is a full 40-character id, `base` is `{ref, head}` with both
   present, `changeRequest` is the forge's address, `checks` is a list of catalogue ids, `reason` is a code from
   the queue's enum, and `log` is a relative path that starts `logs/`.
5. `interrupted` joins the queue's stop reasons, so every stop has a code.
6. `because` and `answers` are lists of earlier entry ids in the same journal. The state tool refuses an id that
   does not exist or is not earlier. `because` may be given on any entry. `answers` is allowed only between the
   pairs of kinds the registry lists, and is refused otherwise. The registry marks `because` required only where it
   always applies; a cause the table gives with a condition is the writer's to supply and its story's to prove.
7. A batch names its members in `data.batch`, in order.

The registry:

8. One file, `skills/orchestrate-work/references/journal-kinds.json`, schema `journal-kinds/1`, lists every kind:
   its `data` shape with each field typed or marked prose, whether `because` is required, the kinds it may answer,
   whether it opens a thread, its `altitude` (`headline`, `story` or `bookkeeping`), its `tone` (landed, ready,
   decision, problem or note), its `label`, its `head` and `line` words, its `explain` sentence, and its tracker
   update: where it posts (`ticket`, `epic`, `ticket-or-epic` or nowhere) and under what heading. It is the only
   place a kind is described. The journal schema's kind list, the guide's tracker table, the tracker publisher, the
   page's labels, marks and tabs, and the narrator all take it from there. A kind of `bookkeeping` altitude may have
   neither the `problem` tone nor a tracker update, and the registry's own test refuses one that has either.
9. Words are templates with named fields and a closed set of forms: a short commit, a lane's name, a count with its
   noun, a list, a duration, a ticket. A part in square brackets is left out when a field it uses is absent. A kind
   may give variants chosen by a field's value. There is no other logic in a template.
10. Kinds beginning `x.` are reserved for an adopter's own registry, which this delivery does not build. The state
    tool refuses one with an error of its own that names the reservation, and the registry's test fails if a
    registered kind begins `x.`.
11. The tools find the registry at one path, which a test may replace. Whatever reads a kind's description reads it
    through that path, which is how the planted-registry rows below prove no surface keeps a copy.

The narrator:

12. One implementation, in Python, at `skills/orchestrate-work/scripts/narrator.py`. It reads a journal, the
    registry, the record's state and the plan, and writes `story/1`. It calls no model, no network and no clock;
    the same record always gives the same story.
13. An entry's instant is its `occurredAt` when it has one, and its `at` otherwise. Order, chapters and costs all
    use that instant.
14. A chapter ends at each `candidate.merged` and is named for the piece that landed. The entries before the first
    landing are "Getting started", and those after the last are "Finishing" while open and "Finished" once closed.
15. A thread opens at an entry of a kind the registry marks as opening one, when no thread already holds it. Three
    kinds open a thread: `candidate.stopped`, `base.broken` and `blocker.recorded`. A stop opens one only when it
    leaves the piece stopped; one whose `requeued` is true left the piece in the queue, opens none, and reads at the
    story altitude. The thread gathers every entry
    linked to its opening through `because` and `answers`, in either direction and at any distance. An entry that
    could open a thread but is reached this way from an earlier opening joins that thread and opens none. The
    thread closes at the first entry that answers its opening. An entry that answers another member joins and
    closes nothing. Its cost is the time between the opening and the closing. A thread with no closing is open: it
    carries the instant it opened and no cost.
16. A beat is one entry, with three exceptions: a batch's starts are one beat, a run of bookkeeping is one beat,
    and a closed or open thread at the `headline` altitude is one beat that tells its opening, its cause and its
    ending. A `decision.recorded` is always a beat of its own, with its reason, even inside a thread.
17. A thread's beat sits at the instant the thread opened, in the chapter running then. Each later chapter that
    begins while it is still open carries one line saying it still stands.
18. The digest states what the story holds: pieces landed, the start and, once closed, the close; setbacks closed
    and their total cost; setbacks still standing, each with its opening instant; and the lead's decisions.
19. The story holds instants and whole durations between two entries. It never holds a time measured to now. A view
    that shows how long something has stood works it out from the instant when it draws.
20. The live view carries the story with the record: each write re-renders, as today, and the stream sends the new
    story. The page draws it and words nothing itself.

The views:

21. "What happened" becomes the story: the digest, then chapters newest first, with a switch for Headlines, Story or
    Everything. The page opens on Story for an open delivery and on Headlines for a closed one, and remembers the
    reader's choice.
22. A thread is drawn once, as a card: what broke, why, what it held up, how it ended and what it cost. Its entries
    do not also appear as loose rows at the headline altitude. At the story altitude they stay in their own places
    in time, marked as the thread's.
23. Any beat opens to its facts: the fields of its entries, with commits, change requests and tickets linked, and
    the kind's `explain` sentence.
24. The tabs for decisions, landings and problems are kept, and are filters over the same story. A kind's label,
    mark and tab come from the story, so the page draws a kind none of its scripts names.

The delivery:

25. The break is made in one pass with a bridge that never ships. From JF-1 to JF-7 the schema still allows
    `summary`, the old top-level members and the old kind `review.evidence`, and `append_journal` still takes a
    sentence. An entry with `data` is checked in full whether or not it also has a sentence; an entry with only a
    sentence is not checked beyond today's rules. Until JF-7 a writer moved to facts still passes its sentence as
    well, so its entries carry both and no reader that has not yet moved breaks. A reader moved to fields reads them
    when the entry has `data` and the sentence otherwise. JF-7 removes the sentence from every writer and the
    second path from every reader, in all three scripts, along with the schema's allowances.
26. What replaces "no sentence" as an assertion is a mechanism, not a search for a word, since review findings and
    tickets rightly have summaries. One test, added by JF-7, walks every call that writes an entry. It knows three
    shapes: `append_journal(…)` in `delivery_state.py` and in `merge_queue.py`'s one direct use,
    `Runner.journal(…)` in `merge_queue.py`, and `state_tool("journal", "--kind", …)` in `run_delivery.py`. It
    counts call expressions. At each, the kind must be a string literal the registry lists, or, at a call the
    inventory marks as choosing, a value that can only be one of a set of literals at that call, each registered.
    Two things are exempt and named in the test: the `journal` command's own call, whose kind the state tool checks
    at run time, and a helper that passes on a kind it was given, such as `Runner.journal`; every call to such a
    helper is walked and counted as a write. The walk fails on a call that passes a sentence, on a kind outside
    those rules, and when a script's count differs from the inventory's. It also fails on a test under `tests/`
    that passes `--summary` to the `journal` command or a sentence to `append_journal`.
27. A sentence pin is replaced by an assertion at least as strong: the entry's kind, the exact value of every field,
    and its links. When a writer's story hands off, no test among its files journals a sentence, whether it
    asserted it or only wrote it. Wording is asserted in the registry's vectors: for every kind, each bracketed
    part both present and absent, each variant, and each form at its edges. No story adds a fixture that starts a
    process. The suite ran at 1,702s of 1,740s on 2026-11-15.
28. This delivery ends merge-ready, on the integration branch `integrate/delivery-story` with `main` as its target.
    It does not merge into `main`, tag or release. When the operator merges it, the release is a major version: an
    adopter's tool that journals with `--summary`, or reads `summary`, must change. It goes out as a candidate
    first, and the candidate is tried on a delivery here before the final is tagged. Those steps are not this
    delivery's.
29. The page rework on `claude/live-view-ui` is this delivery's first story, JF-0, so the story chapter is built on
    it and the operator sees one design, not two.

## The kinds

Altitude is `H` for headline, `S` for story and `B` for bookkeeping. Tone is `landed`, `ready`, `decision`,
`problem` or `note`. A field in italics is prose; every other field is typed. "Links" gives what the kind must or may
name: "cause" is `because`, and "answers" is `answers`. Tracker is where the kind posts and its heading; a dash
posts nothing.

| Kind | Alt | Tone | `data` | Links | Tracker |
| --- | --- | --- | --- | --- | --- |
| `record.started` | H | note | `base` | | epic: Delivery started |
| `story.added` | H | decision | *`title`*, *`why`* | | epic: Story added |
| `lane.assigned` | S | note | *`title`*, *`note?`* | | ticket: Started |
| `review.round` | S | note | `round`, `startedAt`, `finishedAt`, `verdicts`, `findings`, `refused?`, `restarts?` | | - |
| `review.recorded` | B | note | `digest`, `candidateRef` | | - |
| `review.carried` | S | decision | `from`, `to`, `digest`, `baseCommits?`, *`why`* | | ticket: Review carried |
| `candidate.handed` | S | ready | `head`, `changeRequest`, `rounds`, `checks`, `evidence?`, `noChange?` | answers the stop standing for its subject, when one does | ticket: Handed off and queued |
| `handoff.checked` | S | decision | `head`, `rounds`, `afterMerge`, `noChange`, *`notes`* | cause: the hand-off | - |
| `handoff.accepted` | S | decision | `settled` | cause: the `handoff.checked` for this hand-off, when one was journaled; the state tool finds it | ticket: Accepted |
| `handoff.rejected` | H | problem | *`why`* | cause: the hand-off | ticket: Hand-off rejected |
| `handoff.requeued` | S | decision | `head` | answers the stop | ticket: Back in the queue |
| `check.selected` | S | note | `head`, `checks`, *`why`*, `added` | | - |
| `check.selection.cleared` | S | note | | | - |
| `selection.refused` | H | problem | *`detail`* | | - |
| `check.started` | S | note | `batch?` | | - |
| `check.finished` | S | note | `batch?` | cause: its start | - |
| `check.failed` | S | problem | `check`, `timedOut`, `failing`, `more`, `log` | cause: its start | - |
| `check.retried` | S | note | `check`, `failing`, `flake`, `onBase` | cause: the failure | ticket-or-epic: Check retried |
| `check.reused` | S | note | `check`, `head`, `from` | | - |
| `check.carried` | S | note | `checks`, `ranAgain`, `base`, `commits` | | - |
| `candidate.merged` | H | landed | `head`, `recovery?` | | ticket: Merged |
| `candidate.stopped` | H | problem | `reason`, *`detail`*, `requeued?`, `stage?` | cause: the failed check, when the reason is `check_failed`; the break, when it is `base_broken`; none otherwise. Opens a thread unless `requeued` | ticket: Stopped |
| `base.rerun.started` | S | note | `checks`, `base` | cause: the failure | - |
| `base.rerun.finished` | S | note | `base`, `seconds`, `verdict`, `results` | cause: its start | - |
| `base.rerun.stopped` | S | problem | `base`, `seconds`, *`why`* | cause: its start | - |
| `base.broken` | H | problem | `source`, `reason?`, *`detail?`*, `head?`, `after?` | cause: the rerun's verdict, when a rerun declared it; none otherwise. Opens a thread | epic: The delivery base is broken; the queue is stopped |
| `base.repaired` | H | landed | `source`, `head`, `repair?`, *`why?`* | answers the break | epic: The delivery base is repaired |
| `base.verified` | S | note | `source`, `head`, `after?` | | - |
| `base.flaky` | S | problem | `source`, `head`, `after?`, `failing` | | - |
| `base.moved` | S | note | `from`, `to`, `merged`, *`why`* | | epic: Delivery base updated |
| `base.published` | B | note | `base`, `from` | | - |
| `base.merge.authorized` | S | decision | `reviewed`, `baseCommits`, `token` | | - |
| `ownership.granted`, `ownership.revoked` | B | note | `path`, `span?`, `base?`, `movedFrom?` | | - |
| `ownership.claimed` | B | note | `path`, `base` | | - |
| `plan.updated` | B | note | `updated`, `changed` | | - |
| `cleanup.done` | B | note | `base`, `changeRequest?`, `head?` | | - |
| `followup.filed` | B | note | `finding`, `followup`, *`title`* | | - |
| `snapshot.stale` | B | note | `source` | | - |
| `snapshot.retaken` | B | note | `source` | answers the stale note | - |
| `evidence.published` | S | note | `url`, `clips` | | epic: Evidence published |
| `flake.declared` | S | decision | `flake`, `check`, `head` | | epic: Flake declared |
| `fullhealth.skipped` | H | decision | `head`, `posture`, `source`, *`why`* | | epic: Full run skipped |
| `fullhealth.rerun` | S | decision | `head`, *`why`* | cause: the break, when one stands | - |
| `posture.changed` | H | decision | `from`, `to`, `source`, *`why`* | | epic: Posture changed |
| `serving.held` | H | problem | *`why`* | | epic: Serving is held |
| `deferral.breached` | H | problem | `finding`, *`title`* | | ticket: Deferred note still open |
| `blocker.recorded` | H | problem | *`text`* | opens a thread | ticket-or-epic: Blocked |
| `blocker.resolved` | H | landed | *`why`* | answers the blocker or the deferral breach | ticket-or-epic: Blocker resolved |
| `decision.recorded` | H | decision | *`text`*, *`why`* | | ticket-or-epic: Decision |
| `lease.granted` | S | note | `lease`, `holder` | | ticket-or-epic: Lease granted |
| `lease.released` | S | note | `lease`, `holder` | answers the grant | ticket-or-epic: Lease released |
| `release.started` | H | note | `head` | | - |
| `release.finished` | H | landed | `head`, `failedAt?` | cause: its start | epic: Released |
| `finish.met` | H | landed | `finish` | | epic: Finish line met |
| `hook.held` | S | note | `hook`, `verdict` | | - |
| `peer.merged` | S | note | `peer`, `head`, `files` | | - |
| `delivery.closed` | H | landed | `skipped?` | | epic: Delivery closed |

The pairs `answers` allows are exactly those in the Links column: a hand-off or a re-queue answers a stop, a repair
answers a break, a resolution answers a blocker or a deferral breach, a retaken snapshot answers a stale one, and a released lease
answers its grant. Only the first three close a thread, since only their targets open one.

A kind's row in the registry also carries its words and its explanation. Two, as they will read:

- `candidate.handed`: head "{lane} handed off {subject}", line "After {rounds} review rounds[, with {checks} run by
  the lane]". Explains: "A lane says its piece is ready. The lead checks the claim before anything is run on it."
- `base.broken`: head "The main line broke", line "{detail}". Explains: "A check failed on the main line itself, not
  only on a piece. Nothing lands until it is repaired."

## Where entries are written

The inventory Decision 26 holds the writers to. Each line is one call that writes an entry, by the function or
command it sits in, with the kind it writes after this delivery. A line marked "chooses" is one call whose kind is
one of those listed. `lease.granted` and `lease.released` have no call: the lead journals them by hand through the
`journal` command.

`delivery_state.py`, 11 calls:

- `complete_base_move_locked`: `base.moved`
- `handoff`: `candidate.handed`
- `handoff`, no change: `candidate.handed`
- `handoff --accept`: `handoff.accepted`
- `handoff --reject`: `handoff.rejected`
- `select`, chooses: `check.selected` or `check.selection.cleared`
- `capture-review`: `review.recorded`
- `carry-review`: `review.carried`
- `reconcile`, the recovered merge: `candidate.merged`
- the snapshot note, chooses: `snapshot.stale` or `snapshot.retaken`
- the `journal` command, exempt: the kind it is given

`merge_queue.py`, 23 calls:

- `recover_interrupted`: `candidate.stopped`
- `check`: `check.started`, `check.finished`
- `carry`: `check.carried`
- `run_checks`: `check.reused`, `check.retried`, `check.failed`
- `rerun_on_base`: `base.rerun.started`, `check.retried`
- `end_rerun`, chooses: `base.rerun.finished` or `base.rerun.stopped`
- `merge`: `candidate.merged`, `base.repaired`
- `batch_round`: `candidate.stopped` for a member queued again, `check.started`, `check.finished`, `base.broken`
- `release`: `release.started`, `release.finished` when it stops, `release.finished` when it ends
- `stopped`: `base.broken`, `selection.refused`, `candidate.stopped`
- `hold_for_setup`: `serving.held`

`run_delivery.py`, 31 calls:

- `review-reduce`: `review.round`
- `lead-evidence`: `evidence.published`
- `lead-authorize-base-merge`: `base.merge.authorized`
- `rebuild_plan`: `plan.updated`
- `lead-plan` and `_register_stories`: `story.added`, one call each
- `lead-init`: `record.started`
- `lead-push`: `base.published`
- `lead-flake`: `flake.declared`
- `lead-own`, a region, chooses: `ownership.granted` or `ownership.revoked`
- `lead-own`, a whole file, chooses: `ownership.granted` or `ownership.revoked`
- `lead-own --claim`: `ownership.claimed`
- `lead-assign`: `lane.assigned`
- `lead-accept`, no change: `handoff.checked`
- `lead-accept`: `handoff.checked`
- `lead-accept --requeue`: `handoff.requeued`
- `lead-cleanup`: `cleanup.done`
- the supervisor's `finish` step: `finish.met`
- `hook lead.may-stop`: `hook.held`
- the close-out's posture note: `posture.changed`, two calls
- the close-out's skipped run: `fullhealth.skipped`
- `record_full_health`, chooses: `base.broken`, `base.flaky`, `base.repaired` or `base.verified`
- the supervisor's `deferrals` step: `deferral.breached`
- the close-out's follow-ups: `followup.filed`
- the close-out's `closed` step: `delivery.closed`
- the supervisor's `peers` step: `peer.merged`
- `lead-resolve full-health`: `fullhealth.rerun`
- `lead-resolve`, a blocker or a deferral breach: `blocker.resolved`
- `lead-decide`, new: `decision.recorded`
- `lead-block`, new: `blocker.recorded`

The counts are of calls as they will stand after each writer's story. Each writer's story re-derives its count from
the code it finds and corrects this list in its hand-off if the two differ, with the reason.

## Out of scope

- Reading or converting `delivery-journal/1`. Records already archived stay as files; the history page lists those
  it cannot read by name, with the release that wrote them.
- An adopter's own kinds. The `x.` prefix is reserved and nothing more.
- A model writing any part of the story.
- The other chapters of the live page, beyond taking their labels from the registry where they show a kind.
- This repository's own binding and settings. No story edits `.agents/` or `catalog.json`; the lead writes catalog
  and profile wording once, at integration.
- Merging into `main`, tagging a candidate, applying it here and running a delivery on it. All follow the
  operator's look at the design, by Decision 28.

## Approval Handoffs

- The operator has approved delivering this plan once its lenses align, with the three lanes of the last delivery,
  each granted what it needs to land its stories.
- The lead reads the registry's words and explanations as JF-1 hands them off, and the last delivery's headline
  story as JF-2 hands it off, and accepts them or sends them back. The operator sees both in the design.
- The operator sees the design running before anything merges into `main`, and the merge is theirs.
- The lead holds the operator's authority to make the judgement calls that land the delivery on its integration
  branch, and records each as a decision with its reason. Raising a number in `tests/suite-budget.json` is not
  among them.

## Execution posture and sensors

Test-first throughout: the registry's vectors, each writer's entry and the story for each scenario are written
before the code that makes them pass. Nothing is characterized, because no existing behaviour is kept.

Sensors, as `AGENTS.md` names them:

- `python3 -B scripts/validate-registry.py` and `python3 -B scripts/update-provenance.py --check`, on every story.
  The registry, the story's schema and the narrator are new shipped files, so the stories that add them add their
  entries to `provenance.lock.json` by hand and refresh digests with the writer.
- Each story's own test modules, through `scripts/run-tests.py`.
- `tests.test_orchestration_state` on every story that edits the orchestration guide or a skill page.
- `tests.test_delivery_views_page` for the page story.
- The full suite once, at the integration candidate, inside `tests/suite-budget.json`. The base keeps the
  `focused` posture until then.

Shared resources: none new. No story adds a port, a fixed path or a serial unit.

Pins that go stale and are re-derived by the story named:

- The journal schema's members and kind list in `tests/test_orchestration_state.py`: by JF-1.
- The guide's tracker table and the two tests that hold it to the schema's kinds and to the publisher's table, in
  `tests/test_orchestration_state.py`: by JF-1, which derives the guide's table from the registry and holds the
  publisher's table to the bridge's kinds only, until JF-5 replaces that table with a read of the registry.
- Every sentence a test journals or asserts in `tests/test_orchestration_state.py`, `tests/test_merge_occurrence.py`,
  `tests/test_orchestration_adopter.py`, `tests/test_lane_rows.py` and `tests/test_runless_reviews.py`: by JF-3.
  Those in `tests/test_merge_queue.py`: by JF-4. Those in `tests/test_run_delivery.py`,
  `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_peers.py`, `tests/test_run_delivery_hooks.py`
  and `tests/test_run_delivery_tracker.py`: by JF-5.
- `tests/fixtures/delivery-views/line-map/source/journal.jsonl`, every golden built from it, and the plan-update
  entry `tests/test_delivery_views_plan.py` journals: by JF-6.
- The count and names of shipped files wherever a test lists them: by the story that adds the file.

## Scenarios

For the record and its writers. Each refusal appends nothing and names what is wrong:

- An entry whose `data` fits its kind is appended. One with a missing member, an unknown member or an unknown kind
  is refused.
- An `x.` kind is refused with the reservation's own error, not the unknown-kind error.
- A `head` that is not a full commit id, a `base` without its `ref` or its `head`, a `reason` outside the queue's
  enum and a `log` that does not start `logs/` are each refused, and each is accepted in its right form.
- `because` naming a later or missing entry is refused. A kind the registry requires a cause for is refused without
  one. `answers` between two kinds the registry does not pair is refused.
- A decision whose text or reason is empty or only spaces is refused.
- After JF-7, an entry with a `summary` or without `data` is refused, naming the release that reads it; a record
  with an empty journal is accepted.
- A candidate stopped by a failed check names that failure. A new hand-off for its subject answers the stop, and so
  does a re-queue.
- A candidate stopped because the base broke names the break and no check.
- An interrupted stop, and a break `fullHealth` found, name no cause.
- A batch member that did not land and is queued again, and a piece a refusal leaves queued, carry `requeued`.
- An acceptance names the `handoff.checked` journaled for its hand-off, and with none journaled names nothing and
  is appended.
- A base broken after a rerun names the rerun's verdict. Its repair answers the break. A request to run
  `fullHealth` again names the break and answers nothing.
- A batch of three journals three starts whose `batch` lists the same three subjects.

For the narrator, each from a named fixture journal under `tests/fixtures/narrator/`:

- `last-delivery`: a chapter for its start, one for each of its six landings and one for its close; two threads,
  each closed with a cost; a digest that counts them.
- `just-started`: no entries after its start. One chapter, and a digest that says nothing has landed.
- `standing-break`: a break with no repair. An open thread carrying its opening instant and no cost, a digest that
  counts it as standing, and one line in each later chapter.
- `overlapping-breaks`: two breaks overlapping in time are two threads, each with only its own entries.
- `uncaused-stop`: a stop that names no cause opens a thread alone.
- `stop-then-handoff`: a stop answered by a new hand-off closes there.
- `two-answers`: a stop answered by a hand-off and later by a re-queue closes at the first, and its cost runs to
  the first.
- `break-stops-a-piece`: a stop caused by a break joins the break's thread and opens none. A hand-off that answers
  that stop joins and closes nothing. The repair closes the thread.
- `failed-check-first`: the failed check comes before the stop it caused. The thread opens at the stop, holds the
  check, and closes when the stop is answered.
- `recovered-merge`: a merge whose `occurredAt` is an hour before its `at`. Its chapter's time, its order and the
  duration since it was picked up all use `occurredAt`.
- `bookkeeping-in-a-thread`: a bookkeeping entry whose `because` names a thread's member is absent at the headline
  and story altitudes and present once at everything.
- `decision-on-a-break`: a lead's decision whose `because` names a break is its own beat, with its reason, at every
  altitude, and is counted in the digest.
- `answered-bookkeeping`: a released lease and a retaken snapshot answer their pairs and open no thread.
- `requeued-stop`: a stop whose `requeued` is true opens no thread and is a beat at the story altitude.
- `batch-of-three`: three starts whose `batch` lists the same subjects are one beat naming all three entries, and
  a single candidate's start is its own beat.
- `bookkeeping-run`: consecutive bookkeeping entries are one beat at `everything`; a story-altitude entry between
  them splits the run in two; neither part appears above `everything`.
- `thread-at-story`: at the story altitude each entry of a thread is its own beat in its own place, and each names
  the thread.
- `never-landed`: a closed delivery in which one piece never landed says so in its last chapter.

For the views:

- The page shows the beats the story holds at each altitude, and a thread once.
- Opening a beat shows its entries' fields and the kind's explanation.
- A live write moves the story without a reload.
- The tracker posts a landing and a stop under the registry's headings, in the registry's words, and each kind
  where the registry says: its ticket, the epic, either, or nowhere.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | JF-1, JF-3 | `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/journal_words.py`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_state.py`, `tests/test_merge_occurrence.py`, `tests/test_orchestration_adopter.py`, `tests/test_lane_rows.py`, `tests/test_runless_reviews.py`, `tests/test_lead_wait.py`, `tests/test_journal_kinds.py`, `tests/fixtures/journal-kinds/` |
| 1 | 2 | JF-0, JF-2, JF-6 | `skills/delivery-views/`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/`, `tests/test_delivery_views_page.py`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py`, `tests/test_delivery_views_plan.py`, `skills/orchestrate-work/scripts/narrator.py`, `skills/orchestrate-work/references/story.schema.json`, `tests/test_narrator.py`, `tests/fixtures/narrator/`, `skills/orchestrate-work/scripts/render_state.py`, `tests/test_orchestration_render.py` |
| 2 | 3 | JF-4, JF-5 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py`, `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_peers.py`, `tests/test_run_delivery_hooks.py`, `tests/test_run_delivery_tracker.py` |
| 3 | 1 | JF-7 | `tests/test_journal_shape.py`, `skills/orchestrate-work/scripts/merge_queue.py`, `skills/run-delivery/scripts/run_delivery.py`, `skills/delivery-views/assets/`, `skills/orchestrate-work/scripts/render_state.py`, `tests/` |

The keys are placeholders. Tracker items are filed once the plan is approved, each with its criteria below, and the
headings take their keys then. `provenance.lock.json` is shared; each story takes the lock side that lists its new
files and runs the writer, as `AGENTS.md` describes.

Lane 2 starts at once with JF-0, which touches only the page. JF-1 writes the whole registry and the journal's
schema, so no later story edits either until JF-7. The story's own schema is a separate file, lane 2's. Lane 3
starts, and lane 2 goes on to JF-2, when JF-1 has merged. The narrator is built against fixture
journals in the new shape, so it does not wait for the writers. By Decision 25 the writers' and readers' stories
may merge in any order. JF-7 runs when every other story has merged and lanes 2 and 3 have none left, so lane 1
gains their files for it.

## Stories

### JF-0. Land the page rework the story chapter is built on

Lane 2, tier 1, not blocked. Characterization-first. Files: `skills/delivery-views/`, `tests/delivery_views_page/`,
`tests/fixtures/delivery-views/`, `tests/test_delivery_views_page.py`, `tests/test_delivery_views_render.py`,
`tests/test_delivery_views_history.py` and `tests/test_delivery_views_plan.py`.

The branch `claude/live-view-ui` holds a rework of the live page made with the operator, pass by pass, with no test
run between passes: the Now chapter as one unit, the calmer checking sheet, the shared fold, one tempo for every
animation, the stream from a stop being checked, the humanized proof and review wording, and "What's left" by
piece. The lead commits that branch as it stands. This story merges it into its own branch, then makes the page's
tests and goldens true of it: each pin that the rework changed on purpose is re-derived, and each that it broke by
accident is a defect to fix in the page. The timeline's own wording on that branch matches patterns in sentences
and is replaced whole by JF-6, so this story pins only that the timeline draws, not what it says.

Acceptance criteria:

- `c1` The goldens and re-derived pins are captured from the committed `claude/live-view-ui` tree in a commit of
  their own, before any page source changes. Every later change to page source in the story is a named defect fix
  with a test that was red before it. The hand-off lists those fixes, and the captured goldens change only where a
  listed fix says so.
- `c2` Every test under `tests/delivery_views_page/` and every `tests/test_delivery_views_*.py` module passes, with
  each changed pin's reason given in the hand-off as deliberate or as a defect fixed.
- `c3` The registry and provenance checks pass.

### JF-1. Keep facts and causes in the journal, described by one registry of kinds

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/scripts/journal_words.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_state.py`, `tests/test_journal_kinds.py` and
`tests/fixtures/journal-kinds/`.

Write the registry by Decisions 8 to 11, with every kind in the table: fields typed or marked prose, links,
altitude, tone, label, words, explanation and tracker update. Write `delivery-journal/2` by Decisions 1 to 7, taking
its kinds and their `data` from the registry. `append_journal` takes a kind, `data`, `because` and `answers`, and
checks them. The `journal` command takes `--data`, `--because` and `--answers`, and for a decision or a blocker
`--text` and `--why`. `journal_words.py` turns one entry into its head and line from the registry. The guide's
tracker table is derived from the registry, and the two tests that pin it follow by the stale-pin list above.

The bridge of Decision 25 is in place when this story merges: an entry may carry a sentence, facts, or both, and
one with facts is checked in full.

Acceptance criteria:

- `c1` Every kind in this plan's table is in the registry with each column's value, and the journal schema accepts
  those kinds, plus the bridge's until JF-7. No registered kind begins `x.`, and none of `bookkeeping` altitude has
  the `problem` tone.
- `c2` Each refusal in the record scenarios, other than the old shape's, is refused as stated with nothing
  appended, and each has its accepted form beside it.
- `c3` The vectors hold, for every kind, each bracketed part both present and absent and each variant, and a test
  fails for a bracket or variant no vector reaches. Each form has vectors at its edges: counts of 0, 1 and 2; lists
  of 0, 1, 2 and 3; a duration under a minute and over an hour. `journal_words` gives every vector's words.
- `c4` With the registry's path pointed at a copy that holds one extra kind and one changed head, the state tool
  appends the extra kind and `journal_words` gives the changed words.
- `c5` The guide describes the entry's shape, typed and prose fields, links and their allowed pairs, altitudes, and
  how a kind is added, and its tracker table equals the registry's.

### JF-2. Narrate a delivery from its journal

Lane 2, tier 1, after JF-0 and JF-1. Test-first. Files: `skills/orchestrate-work/scripts/narrator.py`,
`skills/orchestrate-work/references/story.schema.json`, `tests/test_narrator.py` and `tests/fixtures/narrator/`.

Build the narrator by Decisions 12 to 19 and the `story/1` schema it writes, in its own file: the digest, chapters,
beats with their altitude and entries, and threads with their opening, closing, cost and state. Each fixture the
scenarios name is a journal in the new shape; `last-delivery` is this repository's last delivery rewritten by hand.

Acceptance criteria:

- `c1` Each narrator scenario's fixture gives the story the scenario states.
- `c2` `last-delivery`'s headline story is kept as a golden file, which the lead reads and accepts at hand-off.
- `c3` Every fixture narrated with the clock's functions replaced by ones that raise gives its story, and an open
  record's story holds no duration measured to now.
- `c4` Every story the narrator writes in its tests validates against `story.schema.json`, and a story with a
  missing or unknown member is refused by that schema.
- `c5` Every beat names the entries it tells, every entry is in exactly one beat at the `everything` altitude, and
  every entry a thread holds names that thread.
- `c6` With the registry's path pointed at a copy in which a fourth kind opens a thread, an entry of that kind opens
  one.

### JF-3. Write the state tool's own entries as facts, and read them as facts

Lane 1, tier 1, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`tests/test_orchestration_state.py`, `tests/test_merge_occurrence.py`, `tests/test_orchestration_adopter.py`,
`tests/test_lane_rows.py`, `tests/test_runless_reviews.py` and `tests/test_lead_wait.py`.

The state tool's calls in the inventory move to their kinds. A hand-off answers the stop standing for its subject.
Its five readers of sentences read fields instead: `repaired_state`, the recovered-merge check, the snapshot note's
repeat check, `base.moved`'s repeat check and the carried review's note. The events `wait` returns carry each
entry's facts and its words.

Acceptance criteria:

- `c1` For each of the state tool's calls in the inventory, a test appends through that call and asserts the
  entry's kind, the exact value of every field, and its links. An acceptance with no `handoff.checked` journaled
  is appended with no cause, and one with it names it.
- `c2` A hand-off for a subject with a stop standing answers that stop, and one for a subject with none answers
  nothing.
- `c3` Each of the five readers, in a case where it finds what it looks for, gives the same result when every prose
  field of the entries it reads is replaced by other text, and reads an entry that has only a sentence as before.
- `c4` A `wait` event carries `data` and the entry's head and line, and with the registry's path pointed at a copy
  with a changed head, carries the changed words.

### JF-4. Write the merge queue's entries as facts, with their causes

Lane 3, tier 2, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py` and
`tests/test_merge_queue.py`.

`Runner.journal` takes `data` and links, and returns the entry's id so a later entry can name it. Every call in the
inventory moves to its kind, with the links the table gives. The queue sets `requeued` on every stop that leaves
the piece in the queue: a batch member that did not land, and a refusal. The queue's one reader of a sentence, for
a stopped rerun's heads, reads fields.

Acceptance criteria:

- `c1` For each of the queue's calls in the inventory, a test appends through that call and asserts the entry's
  kind, the exact value of every field, and its links.
- `c2` For a candidate stopped by a failed check, and for a base broken after a rerun, following `because` from the
  stop or the break reaches the check's start. A stop for `base_broken` names the break. An interrupted stop and a
  break no rerun declared name no cause. A named repair's merge answers the break. A batch member queued again and
  a refused piece left queued carry `requeued`, and a piece left stopped does not.
- `c3` Every stop carries a `reason` from the queue's enum, the interrupted stop included.
- `c4` A batch's entries list its subjects in `batch`; a single candidate's carry none.
- `c5` The rerun's reader, in a case where it finds what it looks for, gives the same result when every prose field
  of the entries it reads is replaced by other text.

### JF-5. Write run-delivery's entries as facts, and let the lead say why

Lane 3, tier 2, after JF-4. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/references/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_run_delivery_peers.py`, `tests/test_run_delivery_hooks.py` and `tests/test_run_delivery_tracker.py`.

Every call in the inventory moves to its kind, with the links the table gives. `lead-accept --requeue` answers the
stop it resolves, `lead-resolve` answers the blocker or the deferral breach it names, a request to run `fullHealth` again names the break
and answers nothing, and the acceptance names its `handoff.checked`. Two commands are new: `lead-decide --text T
--why W [--because N]` records a decision, and `lead-block --text T [--because N]` a blocker; the guide names them
as the way to record either. Its seven readers of sentences read fields: the base ref at start, the merge that
skipped acceptance, the finish lines already met, the held runner, the standing `fullHealth` break, the deferral
breach and a peer's merge. The tracker publisher's own table of kinds is replaced by a read of the registry, and it
posts each entry's words from `journal_words`.

Acceptance criteria:

- `c1` For each of run-delivery's calls in the inventory, a test appends through that call and asserts the entry's
  kind, the exact value of every field, and its links.
- `c2` A re-queue answers its stop, a resolution answers its blocker, and a `fullHealth` rerun request names the
  standing break and answers nothing.
- `c3` `lead-decide` refuses a decision whose reason is absent, empty or only spaces.
- `c4` Each of the seven readers, in a case where it finds what it looks for, gives the same result when every
  prose field of the entries it reads is replaced by other text, and reads an entry that has only a sentence as
  before.
- `c5` A tracker post for a landing and for a stop is under the registry's heading and in its words, and with the
  registry's path pointed at a copy that changes both, the post changes with them. One kind for each destination
  posts where the registry says: its ticket; the epic; either, both with a subject and without; and a kind with
  none posts nothing. With the copy moving a kind from its ticket to nowhere, that kind's post stops.

### JF-6. Show the story on every view

Lane 2, tier 1, after JF-2, JF-3, JF-4 and JF-5. Test-first. Files:
`skills/orchestrate-work/scripts/render_state.py`, `skills/delivery-views/`, `tests/delivery_views_page/`,
`tests/fixtures/delivery-views/`, `tests/test_orchestration_render.py`, `tests/test_delivery_views_page.py`,
`tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py` and
`tests/test_delivery_views_plan.py`.

Builds on JF-0's layout, shared fold and motion, and follows the design direction above. The renderers run the narrator and embed its story; the live stream sends it with the
record, by Decision 20. "What happened" is rebuilt by Decisions 21 to 24. The page's own labels, marks, tab rules
and every pattern it matches against an entry's text are removed in favour of the story; the checking sheet finds a
batch from `batch`, and the plan chapter finds a plan update by its kind. The Markdown view and the built-in page
print the story's beats. The history page reads the new journal and lists an archive in the old shape by name.

Acceptance criteria:

- `c1` At each altitude the page shows exactly the beats the story holds for it, with a thread drawn once at the
  headline altitude.
- `c2` A beat opens to its entries' fields, linked, and its kind's explanation.
- `c3` Given a story that carries a kind no page script names, the page draws that kind's label, mark and tab from
  the story alone.
- `c4` The story documents the page's tests draw are the narrator's output for named journals under
  `tests/fixtures/narrator/`, and a Python test regenerates each and fails when it differs by a byte.
- `c5` The journals the real queue and run-delivery code write, for a stop then a re-queue and for a break a rerun
  declared then its repair, each narrate to one closed thread with the expected cost. The journals come from
  records existing test classes already build.
- `c6` A write to a live record changes the story on an open page without a reload.
- `c8` Each view's reader of an entry, in a case where it finds what it looks for, gives the same result when every
  prose field of the entries it reads is replaced by other text: the checking sheet's batch, the plan chapter's
  plan update, a stop's notes, and any other the story finds in `skills/delivery-views/` and `render_state.py`,
  listed in its hand-off.
- `c7` The Markdown view and the built-in page show the story's beats, and with the registry's path pointed at a
  copy with a changed head, show the changed words. The history page reads a new-shape archive and names an
  old-shape one, in one test.

### JF-7. Remove the sentence

Lane 1, tier 3, after JF-6. Test-first. Files: `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/delivery-views/assets/`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/` and `tests/test_journal_shape.py`.

Remove the bridge of Decision 25. Every writer stops passing a sentence and every reader loses its second path, in
all three scripts and the views. `append_journal` no longer takes a sentence, the schema no longer allows `summary`,
the old top-level members or `review.evidence`, and the `journal` command no longer takes `--summary`. Add the walk
of Decision 26.

Acceptance criteria:

- `c1` An entry with a `summary` or without `data` is refused, naming the release that reads it, and a record with
  an empty journal is accepted.
- `c2` Neither `delivery-journal/2` nor any registry shape has a `summary` member.
- `c3` The walk of Decision 26 passes on the three scripts and on `tests/`. Planted in each of the three call
  shapes, and once through a helper that passes a kind on, it fails for a call given a sentence, for a kind outside
  its rules, and for a call the inventory does not hold. It fails for a test that journals a sentence.
- `c4` No reader keeps a path for an entry without `data`: each reader's sentence-only case from JF-3 and JF-5 is
  now a refusal at the journal.
- `c5` The guide names no sentence on an entry.

## Calls made, and one left

The operator gave the lead the calls that land this delivery. Each open point of this plan is settled here; the lead
records each in the delivery's journal at the start.

1. Voice: plain and concrete, in the words the live page already uses. A problem with an opening and an ending is a
   "setback". The base is "the main line". A candidate is a "piece".
2. A setback's cost is elapsed time, from its opening to its closing. The card says what it held up, so a reader
   can tell a costly one from a quiet one.
3. The tool refuses a decision without a separate reason. A decision whose reason is only in its text cannot be
   shown as "why" without reading prose, which is what this delivery ends.
4. Archived deliveries in the old shape appear on the history page by name only.
5. The page rework lands as JF-0, by Decision 29.
6. The tracker column stands as written. Bookkeeping posts nothing, which the registry's test holds, and what a
   stakeholder would want to hear of posts under its own heading. A lease is story, not bookkeeping, and posts as
   it does today.

Left to the operator, because only the operator may decide it: suite headroom is 38 seconds. Replacing sentence pins
should not add time, and the narrator's tests start no process. If a unit still goes over, the lead makes other
tests cheaper or stops and asks; it does not raise the budget.
