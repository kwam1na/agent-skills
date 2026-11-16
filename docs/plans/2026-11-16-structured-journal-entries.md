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
   document. The page, the Markdown view, the tracker posts and `lead-wait` stop wording entries themselves.

For this repository's last delivery, the headline altitude of that story would read along these lines. The times
and counts here are illustrative; the story's exact words are settled in JF-2, from a fixture of that record:

> **Six pieces in nine hours.** Two setbacks cost 1h 13m. The lead made four decisions.
>
> **Getting started** · 10:07 am. Three lanes picked up five pieces.
>
> **V26-3425 landed** · 11:50 am, 1h 43m after it was picked up, after 2 review rounds.
>
> **V26-3319 landed** · 1:26 pm, after 2 review rounds.
>
> **The plan grew** · 2:26 pm. The lead added V26-3513, from notes raised on V26-3318.
>
> **V26-3318 landed** · 2:44 pm, after 1 review round.
>
> **A setback: V26-3264 was stopped** · 2:28 pm to 3:28 pm, 1h. A check failed because the guide named a file the
> skill doesn't ship. The lane fixed it in a fifth review round and it went back in the queue.
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

## Finish line

- Both plan lenses align.
- No tool writes or reads a sentence in the journal: `summary` is gone from the schema, the writers and the readers.
- Every entry a tool writes validates against its kind's entry in the registry, and names its cause wherever the
  tables below say it must.
- The narrator produces, from a fixture of that delivery's record, a headline story of the shape above that the
  operator has read and approved, and a story for every state the scenarios name.
- The live page's "What happened" chapter shows the story at three altitudes, each setback as one thread, and an
  explanation for any entry. The Markdown view, the built-in page, the tracker posts and `lead-wait` take their
  words from the narrator.
- The work reaches `main` through a reviewed change request. Before it does, the full local suite passes on macOS
  with Python 3.11 at the integration candidate, run with `--workers 4 --budget tests/suite-budget.json`, and the
  suite stays inside the budget.
- CI's Ubuntu and `cut-release` jobs pass for that push to `main`.
- A candidate of the next major version is applied in this repository and runs one delivery start to close before
  the final is tagged.

## Decisions

The operator approves these with the plan.

The record:

1. The journal becomes `delivery-journal/2`. An entry's members are `id`, `at`, `kind`, `lane`, `subject`, `data`,
   and optionally `because`, `answers` and `occurredAt`. `summary` is removed. `review`, `recovery`, `reviewCarry`,
   `reviewArtifact` and `baseMergeAuthorization` move inside `data` of the kinds that carry them.
2. No path reads `delivery-journal/1`. The state tool refuses such a record, naming the release that can read it. A
   delivery open under an older release closes there before the new one is applied.
3. `decision.recorded` stops being a catch-all. What a tool does gets its own kind, listed below.
   `decision.recorded` and `blocker.recorded` remain for what a person decides or reports, and require `data.text`
   and, for a decision, `data.why`.
4. Field names mean one thing everywhere: `head` is a full commit id, `base` is `{ref, head}`, `changeRequest` is
   the forge's address, `checks` is a list of catalogue ids, `reason` is a code from the queue's enum, `log` is a
   path under the record's `logs`. A commit is always written whole.
5. `interrupted` joins the queue's stop reasons, so every stop has a code.
6. `because` and `answers` are lists of earlier entry ids in the same journal. The state tool refuses an id that
   does not exist or is not earlier. The tables below say which kinds must name a cause; for the rest it is
   optional. The lead's own commands take `--because` and `--answers`.
7. A batch names its members in `data.batch`, in order.

The registry:

8. One file, `skills/orchestrate-work/references/journal-kinds.json`, schema `journal-kinds/1`, lists every kind:
   its `data` shape, whether `because` is required, its `altitude`, its `tone` (landed, ready, decision, problem or
   note), its `label`, its `head` and `line` words, and its `explain` sentence. It is the only place a kind is
   described. The journal schema's kind list, the page's labels, marks and tabs, and the narrator all read it.
9. Words are templates with named fields and a closed set of forms: a short commit, a lane's name, a count with its
   noun, a list, a duration, a ticket. A part in square brackets is left out when a field it uses is absent. A kind
   may give variants chosen by a field's value. There is no other logic in a template.
10. Kinds beginning `x.` are reserved for an adopter's own registry, which this delivery does not build. The state
    tool refuses them until it exists.

The narrator:

11. One implementation, in Python, at `skills/orchestrate-work/scripts/narrator.py`. It reads a journal, the
    registry, the record's state and the plan, and writes `story/1`. It calls no model and no network; the same
    record always gives the same story.
12. A chapter ends at each `candidate.merged` and is named for the piece that landed. The entries before the first
    landing are "Getting started", and those after the last are "Finishing" while open and "Finished" once closed.
13. A thread opens at an entry whose tone is `problem` and that no earlier thread already holds, gathers every entry
    linked to it through `because` and `answers` in either direction, and closes at the first entry that answers its
    opening. Its cost is the time between the two. A thread with no answer is open, and says so.
14. A beat is one entry, except that a batch's starts are one beat, a run of bookkeeping is one beat, and a thread's
    entries at the `headline` altitude are one beat that tells the thread whole.
15. The digest counts what the story holds: pieces landed and the time from start to close or to now, setbacks and
    their total cost, and the lead's decisions. It states only what the entries show.
16. Times in the story are instants. The page writes "3 hours ago" and local clock times from them, as it does now.
17. The live view carries the story with the record: each write re-renders, as today, and the stream sends the new
    story. The page draws it and words nothing itself.

The views:

18. "What happened" becomes the story: the digest, then chapters newest first, with a switch for Headlines, Story or
    Everything. The page opens on Story for an open delivery and on Headlines for a closed one, and remembers the
    reader's choice.
19. A thread is drawn once, as a card: what broke, why, what it held up, how it ended and what it cost. Its entries
    do not also appear as loose rows at the same altitude.
20. Any beat opens to its facts: the fields of its entries, with commits, change requests and tickets linked, and
    the kind's `explain` sentence.
21. The tabs for decisions, landings and problems are kept, and are filters over the same story.

The delivery:

22. The break is made in one pass with a bridge that never ships. The first story lets `append_journal` take either
    shape, so the base stays green while the writers move. The last story removes the bridge and `summary`, and a
    test fails while either is named anywhere in `skills/`.
23. Sentence pins in tests are replaced, not added to. A writer's test asserts its entry's kind, fields and links.
    Wording is asserted once, in the registry's vectors: for every kind, at least one entry and the words it gives.
    No story adds a fixture that starts a process. The suite ran at 1,702s of 1,740s on 2026-11-15.
24. The release is a major version. An adopter must act: a tool of theirs that journals with `--summary`, or reads
    `summary`, changes. It goes out as a candidate first.

## The kinds

Altitude is `H` for headline, `S` for story and `B` for bookkeeping. "Cause" marks kinds that must name `because`,
and "answers" what the kind closes.

| Kind | Alt | `data` | Links |
| --- | --- | --- | --- |
| `record.started` | H | `base` | |
| `story.added` | H | `title`, `why` | |
| `lane.assigned` | S | `title`, `note?` | |
| `review.round` | S | `round`, `startedAt`, `finishedAt`, `verdicts`, `findings`, `refused?`, `restarts?` | |
| `review.recorded` | B | `digest`, `candidateRef` | |
| `review.carried` | S | `from`, `to`, `digest`, `baseCommits?`, `why` | |
| `candidate.handed` | S | `head`, `changeRequest`, `rounds`, `checks`, `evidence?`, `noChange?` | |
| `handoff.checked` | S | `head`, `rounds`, `afterMerge`, `noChange`, `notes` | cause: the hand-off |
| `handoff.accepted` | S | `settled` | cause: the check |
| `handoff.rejected` | H | `why` | cause: the hand-off |
| `handoff.requeued` | S | `head` | answers the stop |
| `check.selected` | S | `head`, `checks`, `why`, `added` | |
| `check.selection.cleared` | S | | |
| `selection.refused` | H | `detail` | |
| `check.started`, `check.finished` | S | `batch?` | finished: cause, its start |
| `check.failed` | S | `check`, `timedOut`, `failing`, `more`, `log` | cause: its start |
| `check.retried` | S | `check`, `failing`, `flake`, `onBase` | cause: the failure |
| `check.reused` | S | `check`, `head`, `from` | |
| `check.carried` | S | `checks`, `ranAgain`, `base`, `commits` | |
| `candidate.merged` | H | `head`, `recovery?` | |
| `candidate.stopped` | H | `reason`, `detail`, `requeued?`, `stage?` | cause, when a check failed |
| `base.rerun.started` | S | `checks`, `base` | cause: the failure |
| `base.rerun.finished` | S | `base`, `seconds`, `verdict`, `results` | cause: its start |
| `base.rerun.stopped` | S | `base`, `seconds`, `why` | cause: its start |
| `base.broken` | H | `source`, `reason?`, `detail?`, `head?`, `after?` | cause, when a rerun said so |
| `base.repaired` | H | `source`, `head`, `repair?`, `why?` | answers the break |
| `base.verified`, `base.flaky` | S | `source`, `head`, `after?`, `failing?` | |
| `base.moved` | S | `from`, `to`, `merged`, `why` | |
| `base.published` | B | `base`, `from` | |
| `base.merge.authorized` | S | `reviewed`, `baseCommits`, `token` | |
| `ownership.granted`, `ownership.revoked` | B | `path`, `span?`, `base?`, `movedFrom?` | |
| `ownership.claimed` | B | `path`, `base` | |
| `plan.updated` | B | `updated`, `changed` | |
| `cleanup.done` | B | `base`, `changeRequest?`, `head?` | |
| `followup.filed` | B | `finding`, `followup`, `title` | |
| `snapshot.stale`, `snapshot.retaken` | B | `source` | retaken answers stale |
| `evidence.published` | S | `url`, `clips` | |
| `flake.declared` | S | `flake`, `check`, `head` | |
| `fullhealth.skipped` | H | `head`, `posture`, `source`, `why` | |
| `fullhealth.rerun` | S | `head`, `why` | answers the break, when one stands |
| `posture.changed` | H | `from`, `to`, `source`, `why` | |
| `serving.held` | H | `why` | |
| `deferral.breached` | H | `finding`, `title` | |
| `blocker.recorded` | H | `text` | |
| `blocker.resolved` | H | `why` | answers the blocker |
| `decision.recorded` | H | `text`, `why` | |
| `lease.granted`, `lease.released` | B | `lease`, `holder` | released answers granted |
| `release.started`, `release.finished` | H | `head`, `failedAt?` | finished: cause, its start |
| `finish.met` | H | `finish` | |
| `hook.held` | S | `hook`, `verdict` | |
| `peer.merged` | S | `peer`, `head`, `files` | |
| `delivery.closed` | H | `skipped?` | |

A kind's row in the registry also carries its words and its explanation. Two, as they will read:

- `candidate.handed`: head "{lane} handed off {subject}", line "After {rounds} review rounds[, with {checks} run by
  the lane]". Explains: "A lane says its piece is ready. The lead checks the claim before anything is run on it."
- `base.broken`: head "The main line broke", line "{detail}". Explains: "A check failed on the main line itself, not
  only on a piece. Nothing lands until it is repaired."

## Out of scope

- Reading or converting `delivery-journal/1`. Records already archived stay as files; the history page lists those
  it cannot read by name, with the release that wrote them.
- An adopter's own kinds. The `x.` prefix is reserved and nothing more.
- A model writing any part of the story.
- The other chapters of the live page, beyond taking their labels from the registry where they show a kind.
- This repository's own binding and settings. No story edits `.agents/` or `catalog.json`; the lead writes catalog
  and profile wording once, at integration.

## Approval Handoffs

- The operator approves this plan and its Decisions before any story starts.
- The operator approves the registry's words and explanations as JF-1 hands them off, since they are what every
  reader will see.
- The operator approves the integration change request into `main`.
- The operator approves tagging the candidate, and separately the final after one delivery has run on it.

## Execution posture and sensors

Test-first throughout: the registry's vectors, each writer's entry and the story for each scenario are written
before the code that makes them pass. Nothing is characterized, because no existing behaviour is kept.

Sensors, as `AGENTS.md` names them:

- `python3 -B scripts/validate-registry.py` and `python3 -B scripts/update-provenance.py --check`, on every story.
  The registry and the narrator are new shipped files, so the stories that add them add their entries to
  `provenance.lock.json` by hand and refresh digests with the writer.
- Each story's own test modules, through `scripts/run-tests.py`.
- `tests.test_orchestration_state` on every story that edits the orchestration guide or a skill page.
- `tests.test_delivery_views_page` for the page story.
- The full suite once, at the integration candidate, inside `tests/suite-budget.json`.

Shared resources: none new. No story adds a port, a fixed path or a serial unit.

Pins that go stale and are re-derived by the story named:

- The journal schema's members and kind list, and every exact `summary` asserted, in
  `tests/test_orchestration_state.py`, `tests/test_merge_queue.py`, `tests/test_run_delivery.py`,
  `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_peers.py` and `tests/test_merge_occurrence.py`:
  each by the story that moves that writer.
- `tests/fixtures/delivery-views/line-map/source/journal.jsonl` and every golden built from it: by JF-6.
- The count and names of shipped files wherever a test lists them: by the story that adds the file.

## Scenarios

For the record and its writers:

- An entry whose `data` fits its kind is appended; one with a missing or unknown member, an unknown kind, or an
  `x.` kind is refused, naming what is wrong, with nothing appended.
- `because` naming a later or missing entry is refused. A kind that must name a cause is refused without one.
- A `delivery-journal/1` record is refused whole, with the release that reads it.
- A candidate stopped by a failed check names that failure; its re-queue answers the stop.
- A base broken after a rerun names the rerun's verdict; its repair answers the break.
- A batch of three journals three starts whose `batch` lists the same three subjects.

For the narrator, each from a fixture journal:

- The last delivery's fixture gives one chapter for its start, one for each of its six landings and one for its
  close, two threads, each closed with a cost, and a digest that counts them.
- A record with no entries after its start gives a digest that says nothing has landed and one chapter.
- A break with no repair gives an open thread that says how long it has stood, and a digest that counts it as
  standing, not as cost.
- Two breaks overlapping in time are two threads, each with its own entries.
- A stop whose cause the writer could not name still opens a thread, alone.
- A closed delivery in which one piece never landed says so in its last chapter.
- Bookkeeping never appears above the `everything` altitude, whatever surrounds it.
- A lead's decision appears with its reason at every altitude.

For the views:

- The page shows the same beats the story holds at each altitude, and a thread once.
- Opening a beat shows its entries' fields and the kind's explanation.
- A live write moves the story without a reload.
- The tracker posts a landing and a setback in the narrator's words.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | JF-1, JF-3, JF-7 | `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/journal_words.py`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_state.py`, `tests/test_merge_occurrence.py`, `tests/test_lead_wait.py`, `tests/test_journal_kinds.py`, `tests/fixtures/journal-kinds/` |
| 2 | 2 | JF-2 | `skills/orchestrate-work/scripts/narrator.py`, `skills/orchestrate-work/references/story.schema.json`, `tests/test_narrator.py`, `tests/fixtures/narrator/` |
| 2 | 3 | JF-4, JF-5 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py`, `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_peers.py`, `tests/test_run_delivery_hooks.py`, `tests/test_run_delivery_tracker.py` |
| 3 | 2 | JF-6 | `skills/orchestrate-work/scripts/render_state.py`, `skills/delivery-views/`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/`, `tests/test_orchestration_render.py`, `tests/test_delivery_views_page.py`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py` |

The keys are placeholders. Tracker items are filed once the plan is approved, each with its criteria below, and the
headings take their keys then. `provenance.lock.json` is shared; each story takes the lock side that lists its new
files and runs the writer, as `AGENTS.md` describes.

JF-1 writes the whole registry and the journal's schema, so no later story edits either until JF-7. The story's own
schema is a separate file, lane 2's. Lanes 2 and 3 start when JF-1 has merged. The narrator is built against fixture journals in the new shape, so it does not wait for the writers.

## Stories

### JF-1. Keep facts and causes in the journal, described by one registry of kinds

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/scripts/journal_words.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_state.py`, `tests/test_journal_kinds.py` and
`tests/fixtures/journal-kinds/`.

Write the registry by Decisions 8 to 10, with every kind in the table: fields, altitude, tone, label, words and
explanation. Write `delivery-journal/2` by Decisions 1 to 7, taking its kinds and their `data` from the registry.
`append_journal` takes a kind, `data`, `because` and `answers`, and checks them. The `journal` command takes
`--data`, `--because` and `--answers`, and for a decision or a blocker `--text` and `--why`. `journal_words.py`
turns one entry into its head and line from the registry. The vectors file holds, for every kind, at least one
entry and the words it must give, and a test fails for a kind with none.

By Decision 22, `append_journal` also still takes a sentence, and stores it under a member the schema allows only
until JF-7. Nothing new uses it.

Acceptance criteria:

- `c1` Every kind in this plan's table is in the registry with fields, altitude, tone, label, words and an
  explanation, and the journal schema accepts exactly those kinds.
- `c2` An entry is appended only when its `data` fits its kind, its links name earlier entries, and a cause is
  present where its kind requires one. Each refusal names what is wrong and appends nothing.
- `c3` Every kind has a vector, and `journal_words` gives each vector's words.
- `c4` A `delivery-journal/1` record is refused, naming the release that reads it.
- `c5` The guide describes the entry's shape, the field names, links, altitudes, and how a kind is added.

### JF-2. Narrate a delivery from its journal

Lane 2, tier 2, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/narrator.py`,
`skills/orchestrate-work/references/story.schema.json`, `tests/test_narrator.py` and `tests/fixtures/narrator/`.

Build the narrator by Decisions 11 to 16 and the `story/1` schema it writes, in its own file: the digest, chapters,
beats with their altitude and entries, and threads with their opening, answer, cost and state. The fixtures are
journals in the new shape, one of them this repository's last delivery rewritten by hand.

Acceptance criteria:

- `c1` The fixture of the last delivery gives a chapter for its start, each of its six landings and its close, and
  two closed threads with their costs. Its headline story is kept as a golden file the operator approves at
  hand-off.
- `c2` Each narrator scenario in this plan has a fixture and gives the story the scenario states.
- `c3` The same journal gives the same story on every run, with no network and no clock read.
- `c4` Every beat names the entries it tells, and every entry is in exactly one beat at the `everything` altitude.
- `c5` A thread's cost is the time from its opening to its answer, and an open thread has none.

### JF-3. Write the state tool's own entries as facts, and read them as facts

Lane 1, tier 1, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`tests/test_orchestration_state.py`, `tests/test_merge_occurrence.py` and `tests/test_lead_wait.py`.

The state tool's own entries move to their kinds: both hand-offs, the acceptance and rejection, the check selection
and its clearing, the review record, the carried review, the recovered merge, the snapshot notes and `base.moved`.
Its four readers of sentences read fields instead: `repaired_state`, the recovered-merge check, the snapshot note's
repeat check and `base.moved`'s. The events `wait` returns carry each entry's facts and its words.

Acceptance criteria:

- `c1` Each of those entries is written in its kind with its row's fields and links.
- `c2` None of the four readers names `summary` or matches text, and each one's existing cases pass on fields.
- `c3` A `wait` event carries `data` and the entry's head and line.

### JF-4. Write the merge queue's entries as facts, with their causes

Lane 3, tier 2, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py` and
`tests/test_merge_queue.py`.

`Runner.journal` takes `data` and links, and returns the entry's id so a later entry can name it. Every write site
moves to its kind. A stop names the failure that caused it; a rerun names the failure it follows; a break names the
rerun's verdict; a named repair's merge answers the break. A batch's entries carry `batch`. The queue's one reader
of a sentence, for a stopped rerun's heads, reads fields.

Acceptance criteria:

- `c1` Every entry the queue writes is in its kind with its row's fields.
- `c2` For a candidate stopped by a failed check, and for a base broken after a rerun, following `because` from the
  stop or the break reaches the check's start.
- `c3` Every stop carries a `reason` from the queue's enum, the interrupted stop included.
- `c4` A batch's entries list its subjects in `batch`; a single candidate's carry none.
- `c5` The queue names `summary` nowhere.

### JF-5. Write run-delivery's entries as facts, and let the lead say why

Lane 3, tier 2, after JF-4. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/references/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_run_delivery_peers.py`, `tests/test_run_delivery_hooks.py` and `tests/test_run_delivery_tracker.py`.

Every entry run-delivery writes moves to its kind. `lead-accept --requeue` answers the stop it resolves,
`lead-resolve` answers the blocker or break it names, and the acceptance names the hand-off. The lead's free
decision is `lead-decide --text T --why W [--because N] [--answers N]`, which the guide names as the way to record
one. Its seven readers of sentences read fields: the base ref at start, the merge that skipped acceptance, the
finish lines already met, the held runner, the standing `fullHealth` break, the deferral breach and a peer's merge.
The tracker publisher posts the narrator's words.

Acceptance criteria:

- `c1` Every entry run-delivery writes is in its kind with its row's fields and links.
- `c2` A re-queue answers its stop, and a resolution answers what it resolves.
- `c3` `lead-decide` refuses a decision without a reason.
- `c4` None of the seven readers names `summary` or matches text.
- `c5` A tracker post for a landing and for a setback carries the narrator's words for them.

### JF-6. Show the story on every view

Lane 2, tier 3, after JF-2, JF-3, JF-4 and JF-5. Test-first. Files:
`skills/orchestrate-work/scripts/render_state.py`, `skills/delivery-views/`, `tests/delivery_views_page/`,
`tests/fixtures/delivery-views/`, `tests/test_orchestration_render.py`, `tests/test_delivery_views_page.py`,
`tests/test_delivery_views_render.py` and `tests/test_delivery_views_history.py`.

Depends on the page rework on `claude/live-view-ui` having reached `main`: it holds the layout, the shared fold and
the motion this builds on. The renderers run the narrator and embed its story; the live stream sends it with the
record, by Decision 17. "What happened" is rebuilt by Decisions 18 to 21. The page's own labels, marks, tab rules
and every pattern it matches against an entry's text are removed in favour of the registry and the story; the
checking sheet finds a batch from `batch`. The Markdown view and the built-in page print the story's beats. The
history page reads the new journal and lists an archive in the old shape by name.

Acceptance criteria:

- `c1` At each altitude the page shows exactly the beats the story holds for it, with a thread drawn once.
- `c2` A beat opens to its entries' fields, linked, and its kind's explanation.
- `c3` The page's scripts match no entry's text and hold no table of kinds.
- `c4` A write to a live record changes the story on an open page without a reload.
- `c5` The Markdown view and the built-in page show the story's beats, and the history page names an old-shape
  archive without failing.

### JF-7. Remove the sentence

Lane 1, tier 1, after JF-6. Test-first. Files: `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`
and `tests/test_orchestration_state.py`.

Remove the bridge: `append_journal` no longer takes a sentence, the schema no longer allows the member, and the
`journal` command no longer takes `--summary`.

Acceptance criteria:

- `c1` An entry carrying a sentence is refused.
- `c2` A test fails while any file under `skills/` reads or writes an entry's `summary`.
- `c3` The guide names no sentence on an entry.

## Open questions for the operator

1. The story's words are the product here. Decision 8 puts them in one file and the approval handoffs ask you to
   read them at JF-1. Do you want to set their voice before that, for example whether a setback is called a
   "setback"?
2. Decision 13 costs a setback as the time from its opening to its answer. That is elapsed time, not work lost, and
   pieces unaffected by it kept moving. Is elapsed time the cost you want shown?
3. Decision 3 requires a reason on every decision the lead records. The last delivery's lead recorded several with
   the reason inside the text. Should the tool refuse a decision without a separate reason, as planned, or accept
   one?
4. Decision 2 means archived deliveries, this repository's last two among them, stop appearing on the history page
   except by name. Is that acceptable, or should the history page alone keep reading them?
5. The page rework is unmerged. Should it land on `main` first as its own change, or should JF-6 carry it?
6. Suite headroom is 38 seconds. Replacing sentence pins should not add time, and the narrator's tests start no
   process, but if a unit goes over, raising the budget is yours alone.
