---
name: delivery-views
description: Give a repository that delivers epics through orchestrate-work a ready-made live line map, delivery history page and tracker and forge snapshots, configured rather than coded.
---

# Delivery Views

Ready-made views and snapshot commands for a repository that delivers an epic
through `$orchestrate-work`. The repository writes configuration; it writes no
view or snapshot code of its own. This skill holds everything provider- or
web-specific, so the core workflow stays neutral.

GitHub snapshots retain `mergedAt` from the source. The line map uses a recovered
merge's `occurredAt` for landing times and retains journal `at` as observation
time in the activity feed. A missing source time is shown as unknown. Recovery
does not extend a single-repository snapshot to other repositories: supply a
separate verified forge snapshot for work that the configured repository cannot
list, then reconcile it through the state tool.

Detail times follow the work item's current stage and recorded attempt:
building shows the recorded assignment as Started, review its round start,
queued its hand-off, checking its latest check start, checked its matching
finish, stopped its stop, and landed its merge occurrence. Exact timestamps
and their source appear in tooltips and accessible labels. Missing times stay
unknown; planned work has no start label, and a mutable lane focus time never
supplies a work item's start. The journey's intermediate milestone label uses
its own row so it cannot collide with the finish label.

Each lane's row says how old its words are, from the `focusAt` the state tool
keeps. An active row older than an hour also says it may be out of date; a row at
rest isn't marked, since an old "merged" is still true. A row with no readable
time, as in a record written before the tool kept one, says "No time recorded"
rather than reading as fresh. Words the record derives from the line, for a lane with
no focus of its own, carry no age.

## Dependencies

- `$orchestrate-work` owns the delivery state, its binding, the render wrapper
  and the live server. These views are argv renderers and snapshot commands in
  its binding, run and bounded by its state tool.

## Declaring the views

A repository declares each view as an argv renderer in its binding's `state`,
and may name one as the live server's front page:

```json
"renderers": [
  { "id": "line-map", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/render.py", "line-map", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json", "--state-dir", "{stateDirectory}"], "output": "line-map.html", "timeoutSeconds": 30 }
],
"liveHome": "line-map"
```

Start `serve`, `live` and renders from the repository's top level: the
binding names each script by its path in the repository, and the render
wrapper and the state tool's snapshot commands pass no working directory of
their own. Each
script here then resolves the top level itself (`git rev-parse
--show-toplevel`) and resolves `--config`, `--binding`, `--state-dir` and
`{root}` against it, so run by hand it works from any directory inside the
repository.
A `--config` or `--binding` that is not found exits non-zero naming the path.
The record is the binding's `state.directory` unless `--state-dir` names another;
pass `--state-dir {stateDirectory}` in the renderer and snapshot argv so a
repository serving several records reads, and caches runs for, the one served.

Check a configuration before declaring it:

```bash
python3 -B .agent-skills/current/skills/delivery-views/scripts/views_common.py check --config .agents/delivery-views.json --binding .agents/orchestration.json
```

## The line map

`scripts/render.py line-map` reads the delivery record on stdin and prints one
page: the delivery told in six chapters (now, the line, who, what's left, what
happened, parked), and a seventh, the plan, when the delivery has one (see
"The plan chapter" below), and one more, across repositories, while the record
names sibling deliveries (see "What the views show of siblings" below), with a
detail panel for each piece of work. Its style and
program are the skill's `assets/line-map.css`, `assets/line-map.js`,
`assets/run-summary.css` and `assets/run-summary.js`, inlined into the page, and
it loads only its three typefaces from Google Fonts. It
opts in to the live stream, so `live` serves it at `/view/<output>` and the
page follows every record write; opened as a file, it shows the record as it
was rendered. A history renderer declared in the same binding (`render.py
history`) is linked from the chapters tab and the Go to launcher.

Each chapter is its own page, addressed by the hash (`#now`, `#line`, ...); with
no hash the page opens on Now. A reader steps between pages with the Left and
Right arrow keys, by scrolling on past a page's end or top, from the rail, or
from the chapters tab in the top corner, which lists every page and holds the
switch to one page with every chapter and back, remembered per viewer. The arrow
keys leave the page alone while the reader types in a field, holds a modifier,
has the launcher open, or is in the chapters tab or a row that scrolls sideways.
The Go to launcher, opened with Cmd-K or Ctrl-K, with `/` when not typing, or
from the corner, reaches any page and anything else the page offers by name.
A live page reads its run summaries again from the view the server renders
three seconds after each pushed record, every 30 seconds, and at once on each
`event: runs`, which the server sends only after the views are written.

Now's headline is the delivery's name, under its kicker. Beneath it a status row
says where the delivery stands: up to four marks, with one sentence beside them
naming the stage and who it waits on, and at most one fact under that sentence.
Every word comes from the record, the supervisor's Needs you list and the
forge's facts on the integration's change request; none names a repository's own
check, branch or command.

People never run delivery commands by hand; the lead does. So no word the page
shows a person names a command or its flags, in Now, Needs you, the checks or a
close that stopped partway: it says what the lead does next ("until the lead
restarts them", "Clears when the lead resolves it"), or, when a person must act,
the decision or the step that is theirs, such as marking a change request ready.
The commands stay in each Needs you item's `text` and `clears`, which the lead
reads; its row's `said` words, which the page shows, never carry them.

For the same reason the page never names the supervisor, the lead's background
process: it says what a person notices. "Updates stopped 7 days ago" and "Last
update 20 minutes ago", each "· may be out of date", join "No update for 3
hours"; Needs you is "Hidden while updates are stopped, until the lead restarts
them"; a close that stopped partway is one "the lead finishes by resuming".

- **The marks.** Each is `done`, `live`, `stop` or `todo`, drawn by its shape:
  filled ink, a ring with a halo, the stop colour, a faint ring. The live one is
  labelled with any count, such as "Suite · 34 min" or "Review · round 2", and
  with its holder only when that is you or the forge ("Merge · you"); the lead
  and the lanes go unnamed, since the live ring already says the delivery is
  moving. Its holder stays in the mark's `data-who` either way. While stories
  land the marks are the lanes, each with its landed stories ("Lane 1 · 2 of 4"),
  drawn as rows in lane-number order whose threads curve into the one finish
  after them: Rejoin, or Landed without an integration. Past three lanes, two
  keep their rows, stopped first, then done, then at work, and the rest share
  the third. Once every story has landed on an integration they are Suite,
  Review, Merge and Landed, one after another.
- **The stages.** Stories still landing: "6 of 9 stories are in. A3 is in
  review.", naming the story being checked, in review, queued or built. Then the
  rejoin: the suite runs next; running ("The full suite is running: 28 min,
  about 31 usual."); failed, the lead fixing it, its fact pointing to What
  happened; over budget, "It's your call to accept.", with its time against the
  budget as the fact, until a review starts at that head; the integration review
  in its round; a round that asked for fixes; the change request waiting for your
  merge, or with its checks still running (the forge holds it); the target moved
  and the lead bringing the change request up to date; merged and closing, with
  the follow-ups still to file as the fact; and delivered.
- **The overrides.** A current Needs you item for you takes the sentence, as its
  headline says it, and the live mark becomes yours, with "<mark> waits on you."
  as the fact. A broken base or a held queue becomes the sentence in the stop
  colour, naming the repair the lead marked, with what nothing merges until as
  the fact. A stopped or silent supervisor, or a record with no update for over
  an hour and a half, is the fact in the stop colour: "..., so this page may be
  out of date."
- **The forge's checks.** A failing check on the change request is the fact,
  saying whether the target fails it too: "1 forge check fails, on main too."
  The renderer reads them from the forge snapshot kept at
  `snapshots/forge.json`, from the item whose `changeRequest` is the
  integration's latest, as `checks: {pending, failing: [{name, onTarget}]}`;
  without them Now says nothing of the forge's checks.
- **What never reaches Now.** An item that holds nothing the reader can act on:
  a tracker behind the record, or the target gaining commits the change request
  already holds. Needs you keeps a tracker lag, and it no longer says it holds up
  the finish: the renderer marks it not holding and says the list's opening
  sentence again from the table of wait sentences.

The line draws one row per lane and one main line they merge into, and keeps
the same shape at any delivery size:

- **A lane row** is one width whatever its story count: its landed stories fold
  into one bar sized by their count, with how many under it, then at most three
  open stories keep a stop, in the lane's order, and the rest fold into "+n",
  whose accessible name lists them.
- **The main line** past the join is the delivery's progress: one tick per story
  in its lane's colour, in groups of five. Landed stories come first, then the
  open ones nearest to landing first: in the merge queue, in review, being built,
  stopped, not started. A tick is drawn by its state, a stopped one with a mark
  above it, and a caret marks the story the queue lands next. Over the ticks a
  count line says how many have landed and how many are stopped.
- **The rejoin.** A delivery rejoining its target draws one bar of its four
  steps (main in, suite, review, change request) past the ticks. The active step
  label and status sentence sit below the bar. The line bends into the target
  at the finish post, so the target is drawn once. A delivery
  straight to its base ends at the post, with no bar.
- **A suite running.** From `lead-suite`'s start entry until its finish or a
  later start, the suite's step is drawn wider with a moving stripe, still
  under reduced motion, filling as the run nears its usual time, with a tick
  at that time. The label reads "Rejoin main · suite running" (the target's
  name in place of main), and the sentence under it "The full suite is
  running at <commit>. <n> min, about <m> usual.", with ", so <k> over" once
  past it. A timed suite's finish keeps the wider step: solid when it passed,
  with a cap at its end when it went over its budget, and in the setback
  colour when it failed. A record with no start draws the bar as before.
- **The finish post** carries its label and, beside it, one square per finish
  line, filled once met, with how many are met.
- **Naming and lighting.** Hovering or focusing a tick names its story and state
  and lights its lane; hovering a lane's row lights its ticks. A tick opens its
  story's panel, as a stop does, so a story folded out of its row is still one
  step away. Each step of the bar names its status, and each square its finish
  line. Every tick, step and square is reached from the keyboard, and the
  drawing's accessible name states the landed, met and rejoin counts.

A piece's detail panel says where the story is before it lands, in one state
line with a dot: "Being built", "In review, round N", "Fixing review
findings", "Reviewed, getting ready to hand off", "Waiting for the lead to
accept" or "Being checked before it lands", worked out from the record and its
journal. The dot is green while the story's lane moves it, amber while it waits
on something and grey while the merge queue checks it. Before hand-off the
panel says how the review ended, from the recorded reports: the round it was
approved on, and its notes fixed, deferred to a follow-up or still open. While
the story's lane is on it, the lane's own words follow, with how long ago they
were set. A landed or stopped piece shows none of these. The panel leaves a
unit's proofs to the plan chapter, and the review's count of notes raised to the
recorded reports.

What happened tells the delivery's story at three depths, one row for each beat: its
time, its mark, its sentence and its tone's word. A row opens to nothing; the ticket,
the commit, in full or cut short, and the change request its sentence names are
links, and they lead to the evidence. Only a run of bookkeeping folds, listing its
entries.

Each setback is told once, in a Setbacks ledger above the chapters, from the story's
`ledger`: one row per story and reason. The rows still standing come first, tinted in
the stop colour, saying since when and who they wait on; the ended ones follow,
muted, saying what ended them, when, and what they cost. A row of several stops says
how many in its own words and gives the span from the first to the last. Under each
chapter, one line names each row that opened or ended in it, linked to the row, and
nothing still standing from an earlier chapter is repeated. The line under the title
reads the ledger: "Nothing is stopped now" or how many stand now, then how many are
over and what they cost, then the lead's decisions. The Setbacks filter shows the
ledger and the chapters' setback lines; the others show neither. A story told before
the ledger keeps its own summary and standing lines.

In "Who's doing what", an idle or handed-over lane whose next story waits names that
story once,
then a "Waiting on" row of one pill per story it waits on, nearest to landing first,
from the waits the state tool gives it: each pill is in its lane's colour, filled
while the story is in the merge queue and hollow while it is still with its lane,
with its phase in one word, and a lock when it holds files the next story needs.
One legend names those files. A landed story drops off the row, and the block goes
once the next story starts; any other wait keeps its own sentence. Once the lane's
own story is handed off, its card says "handed off at" its head "after review
round" N, and its chip says "In merge queue" with the runner's phase and how long
the story has been there.

When the record has region claims, so lanes share files by lines, "Who's doing
what" offers "Who holds which files", closed by default. It reads
`lane-changes.json`, which each supervisor pass writes beside the record from
every lane's pushed branch, as run-delivery's configuration reference describes.
It shows the delivery's claimed files grouped by area, the folder under the
repository root they sit in, each in its lane's colour, solid where its lane has
changed it and hollow where it hasn't, and each shared file split by its lanes'
line stretches. Clicking a shared file opens its reading: a ruler of the file with
each lane's stretches and changes, a change outside its lane's span marked, and
per lane the functions it changed with their sizes. A lane whose branch can't be
read is said so, and none of its files is drawn as changed. Before any pass has
read the branches, or with a file that can't be read, the view says so. With no
region claims the page has no such view. The hosted page shows the same view from
the same reads.

A piece whose queue or planned element names a `runId` shows that run in its
detail: its review rounds as a track, and a card for the picked round. With
`runs` configured, the renderer appends these arguments to its argv and reads
JSON from each: `runs list --json` once a render, `runs view <id> --json` for
a run the list shows changed since its cached summary, and `runs artifact <id>
<artifact> --json` for each round report the view references and each brief and
result a report names. A run that cannot be read shows as unavailable; without
`runs` there is no run section at all. All of a render's run commands share the
deadline under "Runs, cache and storage" below; `--deadline-seconds` sets it.

A round card's raised finding keeps how its lens filed it, as `filed` (`deferred`
or `open`) beside the `state` later rounds give it. A round every lens approved
while filing deferrable findings reads "Approved with N deferrable notes", or
"Approved with 1 deferrable note", whether or not a later round fixed them; each
such row reads "Deferred" with its severity's word. A round that filed none reads
"Approved", and one with notes still open "Approved, notes open".

A round whose report names a `briefs` member retains each reviewer's brief and
result in its embedded evidence. The Brief / Result control row is hidden;
reviewer verdicts, findings and fix closures remain visible. Stored evidence is
not deleted. Text remains bounded to 64 KiB at a character boundary. The
embedded run data keeps within 4 MiB: run summaries come first, the oldest
stops' runs becoming unavailable when they alone are over it, then brief and
result texts from the newest round backwards. The record shows what was stored
as each lens's brief and result, as stored; it does not prove the lens received
or followed it.

## The history

`scripts/render.py history` prints the delivery history: past deliveries and
runs, newest first, each opening the line map's run summary in place. Its
style and program are `assets/history.css` and `assets/history.js` with the
run summary's two assets, inlined into the page. Declare it beside the line
map; the line map links to it, and it links back to the binding's line map, as
a file and, when served, at the line map's `/view/` address:

```json
{ "id": "history", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/render.py", "history", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json", "--state-dir", "{stateDirectory}"], "output": "delivery-history.html", "timeoutSeconds": 60 }
```

It lists every run `runs list --json` shows, read with `runs show <id> --json`
(reused from its own cache in the state directory while the inventory shows
the run unchanged), then every `.json` file in the `exports` directory that no
`ignore` pattern matches and the product has not already listed: a run export,
or an older telemetry record (`schemaVersion: 1` with a branch), its ticket
read from the branch by the tracker's key pattern. A run or file
that cannot be read is listed with a note. The record on stdin only names the
delivery each run belongs to. Its run commands share the render's deadline, and
past the 4 MiB page budget the oldest rows give up their summaries first.
`--now` fixes the history's as-of time, for tests.

## The runs page

`scripts/render.py runs` prints the runs page: every run of every workflow the
`runs` command lists, in the line map's typefaces, colour tokens and run
summary. Its style and program are `assets/runs.css` and `assets/runs.js` with
the run summary's two assets, inlined into the page. Declare it beside the line
map, and name it in the binding's `state.liveRuns` so the live server watches
the run store and serves it as the front page while there is no record:

```json
{ "id": "runs", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/render.py", "runs", "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json", "--state-dir", "{stateDirectory}"], "output": "runs.html", "timeoutSeconds": 60 }
```

- "Now" lists open runs, newest first. Each run's line draws its workflow
  stages, each once, in the order the run first entered it, with the current
  one ringed: the innermost stage entered and not yet exited. Stages are read
  only from activities whose `activityId` starts `stage-` and whose phase is
  `route`, `plan`, `diagnose`, `execute`, `review` or `compound`, so a lens
  attempt or the runtime's own `prepare` and `verify` never add one. Under it
  are the run's current activity and next step, and each unresolved wait with
  its reason and next action. Only a wait on a human with no `wait.resolved` of
  the same `waitId` says "Needs you".
- Ended runs follow, newest first, and link to the history. A run left open that a
  later run's start displaced, the runtime recording it as that run's
  `displacedRunId`, is listed with them, marked "Left open, replaced", never
  under "Now".
- Each run opens a panel of its gates (`command`, outcome, duration),
  decisions, blockers, change request, review rounds through the run summary,
  and result with its cost unit and coverage. A run bound to a piece of the
  record names that piece and links to the line map.
- "Plans in review" lists the plan previews the live server serves, which it
  names in the `DELIVERY_PLAN_PREVIEWS` environment variable: each plan's
  title, its preview's live address and, where it is published, its hosted
  page. With none, the section is absent and the page is as before.

Each run is read with `runs show <id> --json` and `runs view <id> --json`, and
cached in the state directory while the inventory shows it unchanged. A run
either can't be read for is shown unavailable, and the others render. Past the
4 MiB budget, ended runs give up their details first, oldest first, then open
ones. When the live server says, in `DELIVERY_LIVE_RUNS`, that the run store
isn't watched, the page says so. Served live, the page reads itself again on
each `event: runs` through the adapter's `onRuns`, keeping open panels open.
`--now` fixes the page's as-of time, for tests.

With no record, the live server gives every view `{"spec":
"delivery-state-absent/1"}`. `runs` renders from it as from a record; `line-map`
and `history` each print one page that says no delivery is under way, links
to the runs page and reloads once a record reaches it. The history also links
to the runs page when the binding declares one.

## The plan chapter

A delivery may also say what it committed to. The line map then gains a
chapter, **The plan**, that answers one question for the engineer working with
the agents: is the work still what was agreed, and what proves it? The chapter
reads one input, `delivery-plan/1`, beside the record the page already reads.
It knows nothing about any repository, tracker, plan format or kind of evidence
beyond what that input declares. `$run-delivery` produces the input; a
repository that writes it any other way gets the same chapter.

### The input: `delivery-plan/1`

The renderer reads `<state directory>/plan.json`: one JSON object, at most
1 MiB, refused unread if larger. Its schema is
`references/delivery-plan.schema.json`. Check a file before relying on it:

```bash
python3 -B .agent-skills/current/skills/delivery-views/scripts/views_common.py check-plan <state directory>/plan.json
```

It exits 0 with a summary, or 2 naming the failing key path without echoing
any value.

| Field | Contents |
| --- | --- |
| `schemaVersion` | `delivery-plan/1`. |
| `updated` | When the producer last wrote it. |
| `outcome` | `{summary, text}`: one sentence, and the full text. |
| `requiredLenses` | Lens ids every approving review round must include. |
| `proofKinds` | Ordered `{id, label, source}`, with `source` either `record` or `producer`. Columns follow this order. |
| `units` | Ordered `{subject, label?, title, removed?, criteria: [{id, text}], proofs: [kind ids owed]}`. |
| `finish` | Ordered `{id, label, text, at, covers: {criteria: [ids] \| "all" \| [], proofs: [kind ids]}}`. `id` is the record's finish line id; `[]` covers no criteria by design, and a line with `[]` and no proofs is the lead's: the supervisor never marks it met. |
| `approved` | `{at, review?, snapshot: {units, finish}}`, frozen once at the start of the delivery. |
| `changes` | Ordered `{id, at, kind, subjects, criteria?, by, why?, title?, source?, ref?, before?, after?}`, `kind` one of `added`, `removed`, `relaxed`, `changed` or `finish`; `by` may be `unrecorded`. |
| `evidence` | `{<subject>: {<producer kind>: {state, head?, at?, reason?, items?}}}`, `state` one of `done`, `todo` or `waived`. `done` and `waived` need `head`; `waived` also needs `reason`. |
| `approvals` | Optional, ordered `{id?, text, state: done \| waiting, who?, blocking?}`. `id` is unique; `blocking` is false when absent. |
| `outOfScope` | Optional list of texts. |
| `planReview` | Optional: how the plan came to be approved. `{lenses, state, endedReason?, rounds}`, below. |
| `closeOut` | Optional: `{fullHealth: "skipped", posture: "focused", source, reason}`, when the close-out's `fullHealth` run was skipped under the base's declared posture. It renders as skipped, and no approval state stands in for it. |
| `preview` | Optional, written only by a plan's preview builder, never by a delivery's producer: `{plan, commit, approval: agents-only \| handed \| endorsed, delivery?, layout: delivery \| other, decisions, endorsements?}`. `delivery` is given exactly when `approval` is `handed`; `decisions` holds at most 100 decisions, each one text or `{head, body?}`: a headline, and beneath it at most 20 paragraphs and `{items}` lists of at most 20 bullets, every text at most 600 characters. With it, the plan chapter says the review is agent-only and never reads as approved. A planning record's plan, the one shared delivery record that exists from a plan's first draft, always carries `endorsements`, possibly empty: each seat's latest endorsement, `{seat, label, commit}`, at most 20, never with `handed`. `endorsed` means every approver seat endorsed `commit`, and needs at least one endorsement; its line reads "Endorsed by <labels> · N review rounds · <ending>". An endorsement of an older commit is shown as stale. A reader that knows its record isn't shared refuses `endorsements`. |

Texts are capped at 600 characters, `outcome.text` at 2,000 and labels at 200;
lists at 200 units, 1,000 criteria, 500 changes and 30 finish lines. Ids match
`[A-Za-z0-9._-]{1,64}` and criterion ids are unique. Unknown keys are refused,
and so is a record kind other than `handed`, `merged` and `review`. Subjects
are data only: the page finds units through its own map, never through a
selector built from one, and sets every text as text. A `ref` becomes a link
only when the renderer already links that change request.

`planReview` holds what the plan file keeps of an unbounded review. `lenses` is
the review's ordered lens ids, 1 to 12, fixed for the review. `state` is
`aligned`, `open` or `ended`, checked against the last entry: `aligned` needs
every lens's verdict `approve` there, `open` at least one that isn't, and
`ended` needs `endedReason`, at most 600 characters. `rounds` holds at most 32
entries, each `{round, fromRound?, candidateRef, at, outcome, verdicts,
findings, raised, settled, clipped?}`: the run summary's round shape. `outcome`
is `approve`, `changes`, `notes-open` when every lens approved and the round
still has a note open and none of them blocks, or null when the round doesn't say.
A round every lens approved with a P0 or P1 still open is `changes`. `verdicts`
maps each lens to `approve`, `changes` or `unavailable`; `findings` counts
`P0` to `P3`; `raised` lists at most 30 `{id, lens, severity, summary, state:
unresolved | resolved}`, summaries at most 280 characters; `settled` lists at
most 30 `{id, lens, state: fixed | dropped | open, fixCommit, resolution}`,
with `fixCommit` a full commit id exactly when the state is `fixed`. A plan has
no deferral path. Entries cover rounds 1 to the last entry's `round` in order,
with no gap or overlap; an entry with `fromRound` is a fold of rounds
`fromRound` to `round`, lists nothing, carries its last round's verdicts and
candidate and its rounds' summed counts, and is never the last entry.
`clipped` is `{findings, closures, texts}`: what the entry doesn't list, and
whether its texts were shortened. Serialized, `planReview` is at most 256 KiB.
run-delivery's producer fits every review to these caps; any producer that
writes the same keys gets the same fold.

With no `plan.json`, the page is exactly as it was: no chapter, no rail entry,
no line in Now, and What's left keeps its finish-line list. An invalid file
renders as if absent, plus the chapter holding one flag, "The plan couldn't be
read", with the key path. A valid plan gets a fixed 512 KiB share of the page,
taken from the runs' budget. Over its share, the plan review's texts are
clipped first, the oldest rounds' resolutions then their summaries, then the
oldest changes' text, then other text outside the approved snapshot. The
snapshot, every count, and each review entry's coverage, verdicts and listed
fix commits are kept, and the changes fold says what was clipped.

### The chapter

It sits between What's left and What happened. Open by default: the title
("N of M criteria proven", adding ", W by waiver" when a proof was waived), the
outcome's summary, what needs the engineer, and the units not yet proven with
each one's next missing proof. With a `planReview`,
one line beside the approval reads "Approved <date> after N review rounds",
then "· every lens aligned", "· ended without alignment" or "· still open"; N
is the last entry's `round`. Without one, the approval keeps its own `review`
text, in the units fold. Folded, each with a count: all units (with an "as
approved / as amended" switch), with a review "Plan review (N rounds)", drawn
by the run summary's track and cards (a fold reads "Rounds a–b" with its
counts, an entry says what it doesn't list, the summary says the review is
unbounded, and an ended review shows its reason), changes since
approval (a scope-over-time chart and word redlines from `before` and
`after`), waived proofs with their reasons, approvals ahead, out of scope, and
the full outcome. Open folds are remembered per viewer under the page's theme
key, and the page works without storage. Now gains the outcome's summary and
"N of M criteria proven · plan changed K times"; each stop on The line opens its
unit; and What's left shows one meter per finish line instead of its
finish-line list, so the meters appear there and not in this chapter. At
phone width each unit becomes a card, with its proofs as labelled pills.

A criterion is **proven**; a finish line is **met**, as the record says.

A journal `delivery.closed` entry makes the page read the delivery as closed,
until a lane is assigned, a candidate is handed or a check starts after it. Now
says so and says when, with no warning about silence, though a lost connection
still shows. Now's sentence says "Delivered <day>: N stories landed." only when
every piece and every live plan unit landed, and "Closed <day>: L of N stories
landed." otherwise; proofs and finish lines keep their own counts beside it. What's left stops
counting down once nothing is left to land, and counts plan units that never reached
the line. Lanes read as finished whatever the record last said they waited on, an
unlanded stop reads "Not landed" and stops pulsing, and leftover holds, leases and
stale sources no longer show. The progress bar and dots use the same count as
Now's sentence, and a piece the plan removed isn't counted. Now and the plan call what
remains left open at close rather than asking for anything. The close is journaled
before the supervisor stops, so its Needs you box still shows while a running,
current list holds the close, and otherwise doesn't show.

### A plan in review

Before any delivery exists, `$run-delivery`'s `plan-view` builds a plan's
preview: the same chapter, from a plan under agent review, with `preview` set.
With it the chapter never reads as approved:

- The approval line reads "Agent review only · not reviewed yet · not approved
  by a person" before the first round, "Agent review only · N review rounds ·
  <ending> · not approved by a person" after one, with the ending words above,
  and "Handed to delivery <key> · N review rounds · <ending>" once a delivery
  takes the review up.
- There is no "as approved / as amended" switch and no changes fold, and the
  plan review speaks of lenses aligning, never of approving.
- A "Decisions" fold lists `preview.decisions`, numbered as the plan numbers
  them, and while the plan records none it says they will show there once it
  does. Each headline always shows, so the calls read at a glance; a decision
  with reasons opens to its paragraphs and bullets, one at a time or all
  together, and "decision N" in its words opens decision N.
- With `layout: other`, one line says the plan isn't in the delivery layout, so
  it shows no stories.
- A preview over its share says so at the chapter's end.

A preview's outcome summary begins "Plan preview (agent review only): ", which
any line map shows as the page's headline. A hosted viewer runs the line map it
was built with, so one built before the release that carries previews ignores
`preview` and draws the approval line as approved: redeploy the viewer from that
release before publishing previews to it.

### Proofs from the record

Record proofs are derived in the page from every record it draws, embedded or
pushed, by the same lookups The line uses:

- A unit whose planned element is `settled`, closed with no change needed, has
  every proof it owes done, and reads "Closed, no change needed", its card
  showing the evidence and the base head it ran at. It counts as landed. One
  handed off with no change and waiting for the lead reads as handed over.
- **`handed`** is done when a queue item exists for the subject, its stage is
  not `stopped`, and it is not pending acceptance.
- **`merged`** is done when the queue item's stage is `merged`. A hand-off after
  a merge supersedes it, and a `merged_unverified` stop leaves it open.
- **`review`** approves from every `requiredLenses` lens, at the queue item's
  `head`: the reviewed and handed head, never `syncedHead`, so a synced or
  merged unit keeps its proofs. Its source is the first that applies: the
  run's latest round, with the journal's verdicts laid over it; an available
  review observation of the queue item's `head`; then the journal's
  `review.round` entries. Rounds the lane only reported carry no verdicts. A
  round that names no candidate counts only when it was recorded after the
  `candidate.handed` before the current one; a unit handed off once counts
  every round. The review reads **stale** only when a source positively shows
  approval for another head, **open** for a round that hasn't approved, and
  **not recorded** for nothing.

Producer proofs come from `evidence`. A missing entry is `todo`. Once the
subject has a queue item, a `done` or `waived` entry whose `head` differs from
that item's `head` is stale and counts as not done; before hand-off an entry
counts at face value, and the unit's open row says "before hand-off".

### Definitions

- A **unit is proven** when every proof it owes is done or waived and none is
  stale; a **criterion is proven** when its unit is. Removed units count in
  neither N nor M.
- A **finish line's meter** fills by the share of covered criteria whose units
  have every `covers.proofs` kind done, and a second segment adds those with
  the kinds done or waived, so the meter and the title agree. A kind a unit
  doesn't owe is settled for it. `"all"` covers every criterion; `[]` covers
  proofs only, over every unit. Its count names the proofs it counts, or
  "Proven" when it names none. A line with `[]` and no proofs has nothing to
  count, so it shows no bar and says so: it is the lead's line, which the
  supervisor never marks met. Whether the line is met comes only from
  the record's finish line with the same id; when the record says met while
  covered proofs are open, the meter says both, and a waived proof counts as
  settled there. With every covered proof settled and the record not saying met, it
  reads "Not marked met". A record finish line with no
  plan entry still shows, with no fill.

### What needs the engineer

The chapter's attention area, headed "The plan needs attention", shows these
flags, each with what settles it, in this order, and with none it reads "Nothing
in the plan needs attention right now." Nobody asked for them: the page found the
plan and the record out of step, so the heading is not the supervisor's "Needs
you".

1. **The plan couldn't be read.**
2. **The base is broken**: the last base event in the record is `base.broken`.
   While it holds, the title can't read "Every criterion is proven".
3. **Approvals are blocking work**: an `approvals` entry is `blocking` and
   `waiting`. The flag names each, in a list, and is headed with the count:
   "An approval is blocking work" or "N approvals are blocking work". A waiting
   approval that isn't blocking raises nothing and stays in "Approvals ahead".
4. **Work in the record isn't in the plan**: a queue or planned subject has no
   unit. The flag names the subjects.
5. **The plan and the record disagree on finish lines**: a finish line in the
   plan that the record lacks, or the reverse.
6. **A finish line no longer covers the plan**: its `covers.criteria` is an
   explicit, non-empty list that omits a criterion added or changed after its
   `at`. Lines covering `"all"` or `[]` never raise it.
7. **A change touches open work**: a `relaxed`, `changed` or `removed` change
   names a unit that is neither proven nor removed. It clears once that unit's
   review approves at a time after the change.
8. **A change has no reason**: its `why` is absent.
9. **A waived proof on a unit not yet proven.**

### Live

The page embeds the validated plan and derives record proofs from each pushed
record, so the chapter redraws as The line does. A producer writes `plan.json`
only when its content, apart from `updated`, changed: atomically first, then a
`plan.updated` entry in the delivery journal, whose facts name the new
`updated` value and what changed. That moves the record, so
the live server renders the page again from the new file.

A page the live server serves follows that entry with no reload. When the latest
`plan.updated` entry names an `updated` value other than the plan in use, the
page fetches its own address and takes the plan from the block the renderer
embedded there, so the renderer's validation is the only validation. It redraws
with the reader's open folds and scroll position kept, and adds nothing to what
the server serves. An answer holding a later plan than the one named is taken,
since the file is written before its entry, and the page stops asking once the
server has gone on holding it for about half a minute. While the server has not
yet rendered the named plan the page keeps asking and says nothing. An answer that
fails, a plan the renderer refused, one the page can't draw, or a render still
behind after about half a minute leaves the current plan on screen with "The plan
shown is out of date." beside the title, and the page keeps asking until the plan
is current. A page opened with no plan has no plan chapter, and gains one only on
a reload.

A static page, one with no address to fetch from or no stream behind it, shows
"The plan changed. Reload to see it." beside the title when the latest "Plan
updated:" entry names an `updated` value other than its embedded plan's; a page
rendered or reloaded after the change shows none.

## Configuration: `delivery-views-config/1`

`.agents/delivery-views.json`; every key but `spec` is optional.

| Key | Contents |
| --- | --- |
| `spec` | `delivery-views-config/1`. |
| `title` | The pages' title, at most 80 characters; `Delivery` when absent. |
| `tracker` | `{name, issueUrl, keyPattern?}`. `issueUrl` takes `{key}`; an item is linked only when `keyPattern`, at most 200 characters, matches the whole key. The host takes no placeholder. |
| `forges` | Each `{name, repository, pullUrl, commitUrl, fileUrl?}`. `pullUrl` takes `{repository}` and `{number}`; `commitUrl` takes `{repository}` and `{sha}`; the optional `fileUrl` takes `{repository}`, `{sha}`, `{path}` and `{line}`, as `https://github.com/{repository}/blob/{sha}/{path}#L{line}` or `https://bitbucket.org/{repository}/src/{sha}/{path}#lines-{line}`. With it, a review note's reference to a file's lines, written `(path:line)` or `(path:start-end)`, opens the file at the commit the note was written about, when the page is made in the repository and the path names exactly one file in that commit. The hosted page links the reference the same way when the forge names a `fileUrl`: the hosted cut carries each such file link, and a viewer deployed before that refuses such a cut, with `invalid_projection`, until it is redeployed. |
| `runs` | `{argv, timeoutSeconds?}`: the command that prints a delivery run, `{root}` being the repository top level; `timeoutSeconds` is 10 when absent. Absent: no run section and no command run. |
| `findings` | `{reportSpecs}`: the round report specs read, in preference order; `["review-round-report/1"]` when absent. |
| `lenses` | `{"<lens id>": {name, icon?}}`. `icon` names a shipped icon (`bullseye`, `spy`, `shield`, `bug`, `gauge`, `lock`, `book`); a lens with none shows its initial. `lens.outcome-correctness` and `lens.adversarial-testing` have defaults. |
| `scrubEnv` | Environment variable names removed from every `runs` child's environment, so provider tokens reach only the snapshot commands. |
| `exports` | History only: `{directory, ignore?}`, a repository-relative directory of exported runs and the file patterns it skips. |

A refusal names the key path and exits non-zero; the render wrapper records
the failed renderer and keeps serving. Refused: an unknown key; a URL template
whose scheme is not `https` or whose host is empty; a placeholder the key does
not take; a `keyPattern` over 200 characters or one that does not compile;
text over its cap.

## Links

Placeholder values are percent-encoded as path segments, and `repository`
keeps its single `/`. A value, or a repository segment, of `.` or `..` renders
as text, since a browser follows such a segment even when encoded. A produced URL is used
only when its scheme, host and port equal the template's, `:443` counting as
no port; otherwise the value renders as text. A change request is linked when
it is `#<n>` or `<repository name>#<n>` on the first forge, or a URL one
forge's `pullUrl` matches; a fix commit only when it is 40 lowercase hex.
A matching URL may name any safe canonical owner/repository on that configured
origin and template; no per-repository configuration is needed. Shared link
metadata carries the number, decoded repository and URL. The page shows `#N`
with the repository in its title and accessible name. Unknown origins, paths
or unsafe encodings remain text.

## Runs, cache and storage

Every `runs` command runs bounded: its own process group, `timeoutSeconds`, a
16 MiB output cap, stdin closed and the `scrubEnv` variables removed. A
timeout, oversize output, a non-zero exit or output that is not JSON makes
that run unavailable and never fails the render. All of a render's runs share
one deadline: the renderer's `--deadline-seconds`, 20 when absent, capped at
the renderer's declared `timeoutSeconds`, read through `--binding`, less 5
seconds. Runs are read six at a time, so one slow or hanging run holds only
its own slot. The line map keeps a run's latest 12 rounds; the history keeps
every round.

The run cache is `<state directory>/.cache/delivery-views/runs.json`, spec
`delivery-views-runs/2`, written atomically; a corrupt cache is rebuilt and an
unwritable one only costs a re-read. A page keeps its theme under
`delivery-views:<first 16 hex of the SHA-256 of the resolved state
directory>:theme`, so two repositories served from one address never share it.

## Snapshot commands

`scripts/snapshot.py --kind jira|bitbucket|linear|github` prints one `work-snapshot/1` for the
binding's `trackerSnapshot` or `forgeSnapshot`, exits 0, and prints nothing
else; on failure stdout is empty, the exit code is non-zero and stderr holds
one line naming the cause. Every kind takes `--timeout-seconds`, one deadline
for the whole run (25 when absent; set it below the binding's
`timeoutSeconds`), and `--now`, the snapshot's time, for tests.

- Credentials come only from the environment variables `--token-env` and
  `--user-env` name; a missing one fails naming it. Requests carry them only
  to the provider's own origin, and a redirect or next page on another origin
  is refused. No token, nor its encoded form, is ever printed.
- Paging runs until done; reaching 500 items fails. HTTP 429 and 5xx are
  retried at most twice, honouring `Retry-After` within the deadline; 401 and
  403 fail at once.
- A field the mapping needs that is missing or unrecognised fails the
  snapshot; nothing is defaulted.

**Jira Cloud** (tracker): `--kind jira --site https://<site>.atlassian.net
--parent {parent} --user-env JIRA_EMAIL --token-env JIRA_API_TOKEN
[--canceled-statuses "Won't Do,Duplicate"] [--blocks-link Blocks]
[--scope "component=Mobile App" ...] [--status-state "Ready for QA=completed" ...]
[--state-dir {stateDirectory}]`. The
parent's children come from the enhanced JQL search, paged by
`nextPageToken`, with Basic email and API token. A status listed as canceled
is `canceled`, after any `--status-state` mapping; otherwise its category maps `new` to `backlog`,
`indeterminate` to `started` and `done` to `completed`. The issue's summary
is the title, its description flattened to plain text of at most 1000
characters is the summary, and `blockedBy` holds the other children linked
inward by the blocks link type. The parent's own status is then read once, by
its key, into `parent`, mapped the same way. When that read fails or the
tracker no longer has the parent, or answers it with something other than
JSON, `parent` is left out and the children's snapshot still prints, so
nothing says the parent closed. A JSON answer with no fields object, or with
a status category it doesn't know, fails the snapshot.

A Jira parent can span repositories, each delivering its own children. Each
`--scope "<kind>=<value>"`, repeatable, keeps the children that carry it:
`component=<name>` (the most reliable, since Jira can require a component),
`label=<name>`, or `prefix=<marker>`, a summary starting with the marker,
ignoring case. With `--state-dir {stateDirectory}`, every story the
delivery's `plan.json` lists stays in scope too, whatever the tracker marks
it with. A child that carries a value of a scoped kind and matches no
`--scope` is listed under `outside`, with its state, status and `marks`, the
values that place it there (for `prefix`, a leading `[Tag]` or `Word:`), and
its summary and blockers.
`reconcile` never adds an outside child as planned work or a follow-up and
never reports on it. It clears a copy the sweep added earlier, planned work
with no lane or a follow-up, journaling `work.cleared` once. It keeps
`outside` in the record, and the line map shows it in a collapsed "Elsewhere
in KEY" fold. An unscoped tracker snapshot empties it. Work of this delivery
that the tracker marks outside is never cleared: queued work, work with a
lane, or planned work with the plan's order, files, change request or run. A
lane-less story of this delivery that the tracker marks outside keeps its
shape: its ticket's start doesn't move it. A child with no value of any scoped kind stays in scope,
flagged `unmarked`. Without `--scope`, every child is in scope, as before.

A child a sibling delivery holds is left to it, with or without `--scope`:
when its subject is in any sibling's last kept read, even while that sibling
can't be read, unless it now reads as another epic's, and no story of this
delivery holds it, `reconcile` never adds
it or reports on it, and clears a copy the sweep added earlier the same way,
journaling `work.cleared` once with "held by sibling <name>". Only a tracker
snapshot's pass clears one. The views draw it in that sibling's group rather
than under "Elsewhere".

Each `--status-state "<status name>=<state>"`, repeatable, reads that status
as `backlog`, `started`, `completed` or `canceled` in place of its category.
Map post-merge statuses in the In Progress category, such as Ready for QA, to
`completed`. Map a status that sends work back, such as QA Return, to
`started`: `reconcile` then reports a merged story whose ticket moves there,
"returned after merge". The snapshot carries the map as `statusStates`.
`--check` prints, in place of the snapshot, what run-delivery's `doctor`
reports: each component, label or mapped status the project doesn't know,
the In Progress statuses left unmapped, and the unmarked children.

**Bitbucket Cloud** (forge): `--kind bitbucket --workspace <workspace>
--repo <repository> --binding .agents/orchestration.json --state-dir
{stateDirectory} --subject-pattern "<regex>" --token-env BITBUCKET_TOKEN [--user-env BITBUCKET_EMAIL]
[--since <ISO time>] [--host bitbucket.org]`. With `--user-env` it uses
Basic (an API token with its email), without it Bearer (a workspace or
repository access token). Pull requests in any state created on or after the
since time (`--since`, else the record's first journal entry) are matched to
a subject the record holds by the pattern on the title, then on the source
branch. For a subject with several, the most recently updated open one wins,
else the most recently updated merged one, else the most recently updated.
`OPEN` is `open` (`draft` when marked draft), `MERGED` is `merged`, and
`DECLINED` and `SUPERSEDED` are `closed`. `changeRequest` is the pull
request's page, kept only on `--host`; `head` is the source commit resolved
to its full hash, left out when it cannot be resolved. A pull request created
before the since time, for a ticket moved into the epic later, needs
`--since`. Keep `--subject-pattern` simple: it runs against every pull
request's title and branch.

**Linear** (tracker): `--kind linear --parent {parent} --token-env
LINEAR_API_KEY [--site https://api.linear.app]`. The parent's children come
from Linear's GraphQL API at `--site`'s `/graphql`, paged by cursor, with the
personal key as the Authorization header; run-delivery's Linear adapter posts
to the same site with the same key. A child listed
twice is kept once. State types map `triage`, `backlog` and `unstarted` to
`backlog`, and `started`, `completed` and `canceled` to themselves. The
summary is the first paragraph under the description's `## Summary` heading,
as plain text, at most 1000 characters, and `blockedBy` holds the issues
related to it as blockers, other than the parent. Any `lin_api_` key in a refusal is redacted.

**GitHub** (forge): `--kind github --owner <owner> --repo <repository>
--binding .agents/orchestration.json --state-dir {stateDirectory}
--subject-pattern "<regex>" --token-env GITHUB_TOKEN [--since <ISO time>] [--host github.com]`. Pull requests come
from the REST API, newest first, paged by the `Link` header, with a Bearer
token, until one was created before the since time (`--since`, else the
record's first journal entry). A pull request counts for each subject its
title opens with, as `[A-1]: ...`, `[A-1, A-2]: ...` or `A-1: ...`, that
the record holds and the pattern matches. For a subject with several, an open
one wins, else the latest merged, else the latest closed, by when it merged
or closed. An open pull request is `open` (`draft` when marked draft), a
closed one `merged` when it merged and `closed` otherwise. `changeRequest` is
its page, kept only on `--host`; `head` is its head commit, left out when it
is not a full hash. A pull request created before the since time, for a
ticket moved into the epic later, needs `--since`.

## Adopting the views

A repository adopts these views with configuration alone: install a release
whose profile includes `delivery-views`, write `.agents/delivery-views.json`,
and declare the snapshot commands, and the views, in the binding's `state`.
It writes no view or snapshot code of its own.

- **Start at the top level.** Run `serve`, `live` and renders from the
  repository's top level, where the binding's script paths lead; the scripts
  resolve every other path in the configuration and the binding from there.
- **Credentials by environment variable.** Export each token the snapshot
  commands name before starting `serve`, which runs them; nothing goes in the
  committed binding or configuration. List the same variables in `scrubEnv`
  so no `runs` child sees them, and start `live`, which runs no snapshot
  command, without them.
- **Check before declaring.** `views_common.py check`, above, validates the
  configuration and names where the views read and write.
- **The record's start.** A forge snapshot takes pull requests created since
  the record's first journal entry. A new record's journal is empty, so give
  `--since` until it has one, and for a ticket moved into the epic with older
  pull requests.

### Jira and Bitbucket

`.agents/delivery-views.json`:

```json
{
  "spec": "delivery-views-config/1",
  "title": "Example app delivery",
  "tracker": {
    "name": "Jira",
    "issueUrl": "https://example.atlassian.net/browse/{key}",
    "keyPattern": "^(APP|ABC)-[0-9]+$"
  },
  "forges": [
    {
      "name": "Bitbucket",
      "repository": "example/app",
      "pullUrl": "https://bitbucket.org/{repository}/pull-requests/{number}",
      "commitUrl": "https://bitbucket.org/{repository}/commits/{sha}"
    }
  ],
  "scrubEnv": [
    "JIRA_API_TOKEN",
    "JIRA_EMAIL",
    "BITBUCKET_TOKEN",
    "BITBUCKET_EMAIL"
  ]
}
```

In the binding's `state`, with `{parent}` the record's `meta.trackerParent`
(here the Jira epic's key):

```json
{
  "renderers": [
    {
      "id": "line-map",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "line-map",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "line-map.html",
      "timeoutSeconds": 30
    },
    {
      "id": "history",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "history",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "delivery-history.html",
      "timeoutSeconds": 60
    }
  ],
  "liveHome": "line-map",
  "trackerSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "jira",
      "--site",
      "https://example.atlassian.net",
      "--parent",
      "{parent}",
      "--user-env",
      "JIRA_EMAIL",
      "--token-env",
      "JIRA_API_TOKEN",
      "--canceled-statuses",
      "Won't Do,Duplicate",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  },
  "forgeSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "bitbucket",
      "--workspace",
      "example",
      "--repo",
      "app",
      "--binding",
      ".agents/orchestration.json",
      "--state-dir",
      "{stateDirectory}",
      "--subject-pattern",
      "(APP|ABC)-[0-9]+",
      "--token-env",
      "BITBUCKET_TOKEN",
      "--user-env",
      "BITBUCKET_EMAIL",
      "--since",
      "2026-10-19T00:00:00Z",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  }
}
```

Export `JIRA_EMAIL` and `JIRA_API_TOKEN` (an Atlassian API token) and
`BITBUCKET_EMAIL` and `BITBUCKET_TOKEN` (an API token used with the email for
Basic authorization; leave out `--user-env` and `BITBUCKET_EMAIL` to use a
workspace or repository access token as Bearer instead).

### Linear and GitHub

`.agents/delivery-views.json`, here with the delivery harness's runs and run
exports:

```json
{
  "spec": "delivery-views-config/1",
  "title": "Delivery",
  "tracker": {
    "name": "Linear",
    "issueUrl": "https://linear.app/example-team/issue/{key}",
    "keyPattern": "^ENG-[0-9]+$"
  },
  "forges": [
    {
      "name": "GitHub",
      "repository": "example-org/example-repo",
      "pullUrl": "https://github.com/{repository}/pull/{number}",
      "commitUrl": "https://github.com/{repository}/commit/{sha}"
    }
  ],
  "runs": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current",
      "--root",
      "{root}",
      "harness"
    ],
    "timeoutSeconds": 10
  },
  "scrubEnv": [
    "LINEAR_API_KEY",
    "GITHUB_TOKEN"
  ],
  "exports": {
    "directory": "telemetry/delivery-runs",
    "ignore": [
      "delivery-record--*"
    ]
  }
}
```

In the binding's `state`, with `{parent}` the Linear parent issue's
identifier:

```json
{
  "renderers": [
    {
      "id": "line-map",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "line-map",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "line-map.html",
      "timeoutSeconds": 30
    },
    {
      "id": "history",
      "argv": [
        "python3",
        "-B",
        ".agent-skills/current/skills/delivery-views/scripts/render.py",
        "history",
        "--config",
        ".agents/delivery-views.json",
        "--binding",
        ".agents/orchestration.json",
        "--state-dir",
        "{stateDirectory}"
      ],
      "output": "delivery-history.html",
      "timeoutSeconds": 60
    }
  ],
  "liveHome": "line-map",
  "trackerSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "linear",
      "--parent",
      "{parent}",
      "--token-env",
      "LINEAR_API_KEY",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  },
  "forgeSnapshot": {
    "argv": [
      "python3",
      "-B",
      ".agent-skills/current/skills/delivery-views/scripts/snapshot.py",
      "--kind",
      "github",
      "--owner",
      "example-org",
      "--repo",
      "example-repo",
      "--binding",
      ".agents/orchestration.json",
      "--state-dir",
      "{stateDirectory}",
      "--subject-pattern",
      "^ENG-[0-9]+$",
      "--token-env",
      "GITHUB_TOKEN",
      "--timeout-seconds",
      "25"
    ],
    "timeoutSeconds": 30
  }
}
```

Export `LINEAR_API_KEY` (a personal API key) and `GITHUB_TOKEN` (a token that
can read the repository's pull requests).

## Round reports: `review-round-report/1`

A round's findings record, one canonical JSON document per closed round:

| Field | Contents |
| --- | --- |
| `spec` | `review-round-report/1`. |
| `runId`, `roundId` | The run and the round, text of at most 128 characters. |
| `ticket` | The work item, free text of at most 64 characters, or null. |
| `round` | The round number, from 1. |
| `candidateTreeSha` | The reviewed tree, lowercase hex. |
| `closedAt` | When the round closed, a UTC timestamp. |
| `raised` | At most 50 findings in file order, each exactly `{id, lens, severity, summary, state, deferredIssue}`: `id` is `r<round>-f<k>`, `severity` P0 to P3, `summary` 1 to 280 code points with no control character or line break, `state` `open`, `deferred` or `dropped`, and `deferredIssue` free text of at most 64 characters exactly when deferred, else null. |
| `resolved` | Earlier rounds' findings settled here, each exactly `{id, state, fixCommit, deferredIssue}`: `state` `fixed`, `dropped` or `deferred`; `fixCommit` 40 lowercase hex exactly when fixed, else null; `deferredIssue` as above. An id is resolved at most once. |
| `briefs` | Optional. `{"<lens id>": {brief, result}}`: the artifact ids of what the lens was told and what it returned, each null when not recorded. A report without it stays valid. |

`report_problem` in `scripts/views_common.py` checks a report and names the
first problem's key path.


## Native reviews without a run

The line map also reads the validated `reviewEvidence` observations supplied by
`delivery_state.py show`. Use orchestrate-work's `capture-review` command with
actual outcome files after hand-off; do not manufacture a `runId`. Shared round
cards show the real lens verdicts, findings, reviewed candidate for each round,
and originating closure text. These reports are for display only. Current
approval requires the final candidate to match the stop's retained head, not
its synced head;
missing, tampered, oversized or stale evidence says unavailable. Live candidate
changes invalidate the displayed approval immediately. Brief / Result controls
remain hidden. Run-backed cards continue to read the configured run store.

## Hosted publishing

Publishing is an opt-in, read-only projection, independent of generic publishers
and tracker writes. Configure an explicit HTTPS DNS origin and repository scope
in `.agents/delivery-views.json`; put the repository-scoped publishing token only
in the named environment variable:

```json
"hosted": {
  "origin": "https://deliveries.example.com",
  "repositoryId": "provisioned-repository-id",
  "tokenEnv": "DELIVERY_PUBLISH_TOKEN"
}
```

The origin takes no credentials, query, fragment, non-default port or path other
than `/`. Publishing is disabled without this member. Any service that conforms
to `hosted-delivery-api/1` is a valid origin: the Cloudflare Worker, the portable
container built from the release's service bundle, or your own implementation
that passes the conformance suite. Do not use a Cloudflare
account-management credential. The transport follows no redirects and ignores
proxy environment configuration. HTTP is available only through an injected
loopback fixture transport, never production configuration or CLI.

The owning lifecycle supplies the canonical delivery key and record directory:

```bash
python3 -B .agent-skills/current/skills/delivery-views/scripts/hosted.py start --config .agents/delivery-views.json --binding .agents/orchestration.json --state-dir .orchestration/state --key APP-1 --generation .agent-skills/current
```

The same arguments work with `status`, `retry` and `stop`. Status and command
errors are JSON with bounded codes; status intentionally returns the shareable
read-only URL. No bearer, raw response or payload is logged. Identity, status,
pending public projection and ownership sidecars live beside the record, contain
no credential, and must remain together when the lifecycle archives or resumes
the record. A corrupt or mismatched identity is refused rather than replaced.
Never delete an identity to work around a conflict: inspect status first.

`start` adopts the same live owner or replaces a verified owner of another
generation. The watcher holds its own exclusive lock and records its PID, process
start identity, script digest and generation identity. Stop checks the held lock
and exact process identity before signalling only that PID. The watcher stops its
recorded collection/upload children on shutdown and exits when its source record
or generation disappears. It never calls generic renderers or tracker publishers.

Changes coalesce for 2 seconds with a 10-second maximum deferral. One collection
or upload runs at a time; a full source check confirms unchanged content every
30 seconds. Collection and upload child processes have total deadlines of 60
and 30 seconds. Transient failures retry with jittered backoff from 2 to 60
seconds. Permanent errors stop network retries until explicit `retry`; credential
changes require `stop` then `start` so the uploader receives the new environment.
An expired session requires explicit `retry --resume`. A fenced owner does not
reacquire or take over another owner, even with that flag. Operator takeover is
not offered by this command.

Every send comes from a fresh coherent cut of canonical state/journal, optional
plan/Needs you paths (including absence), the files view's `lane-changes.json`, the forge snapshot,
selected native review artifacts and fresh run inventory/view/artifact reads checked
again after collection. Never use previously rendered HTML or a cached run byte
count as freshness proof.

The hosted page shows what the local live view shows, from the same record. The
snapshot is the cut the local page renders from: every chapter, the story, each
journal entry with its facts, the integration step and the forge's checks on its change request,
the plan, Needs you, review evidence with its briefs and results, the files view and
what a closed delivery left open, with the paths and commands its prose holds. The
hosted page draws it with the same `line-map.js`, so the two views tell the same
delivery. The one rule kept is that no credential is published. The uploader
scans every projected string and key for its configured secret values before
writing pending bytes or sending them, and a field named password, token, secret,
API key, credential, authorization or cookie, at any depth, is refused; either
refuses the cut with its bounded code (`snapshot.configured_secret`,
`snapshot.credential_field`), which the retried status names. Collection and
run-command children receive none of those credentials. A sibling delivery's hosted
link grants read access to that delivery, so it is a credential too, and the cut
names the sibling without it.
Byte, text and list bounds keep a cut within what the service accepts. The schema
is `hosted-delivery-snapshot/3`, sent as renderer `delivery-line-map/3`; a reader
given another version refuses it. No scanner claims to identify every secret
someone types into prose.

Pending bytes use immutable `hosted-pending-<sha256>.json` files. The identity
pointer and its exact digest/epoch/sequence commit only after those bytes are
durable, so an interrupted replacement preserves the previous referenced body.
An archive needs the body the pending descriptor names, rather than every orphan
from an interrupted pointer write. Source loss cancels active recorded child
groups and prevents later session, collection or transport stages. Configured run
commands remain in the collection child's owned group for this cleanup.
After ambiguous acceptance, authenticated status reconciles the receipt and
identity before any retry. A failed source collection never refreshes source
freshness. Old receipt retries never masquerade as new confirmations. Lost
contact leaves the last accepted remote copy readable; local completion alone
does not establish remote completion. Lifecycle terminal-cut handling, service
qualification and live deployment evidence remain distinct from fixture tests.

The lifecycle adapter posts a credential-free terminal notification before
waiting for owner control. One immutable notice fixes each finished cut's retry
deadline; repeated close requests do not extend it. The existing watcher reads
that notice during contention, preserves acknowledged terminal state, and exits
by the terminal deadline. Only the selected terminal metadata and referenced
bodies enter archives. Live pending status retains the public URL and preceding
receipt; active archive polling tolerates bounded unavailability before strict
post-exit identity/status checks.

### Reading sibling deliveries

`hosted.py siblings` reads the sibling deliveries a record names, each from its
hosted live view, and prints one `sibling-snapshot/1`. Declare it as the
binding's `state.siblingSnapshot`, and `serve` runs it on its refresh:

```json
"siblingSnapshot": {
  "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/hosted.py", "siblings", "--state-dir", "{stateDirectory}"],
  "timeoutSeconds": 60
}
```

It needs no `hosted` member, key or token: it reads public cuts only. Each
sibling is read the way the trusted viewer reads one: `current`, then the body
by its digest, whose bytes must hash to that digest, validate as the
`hosted-delivery-snapshot/2` this generation publishes and carry `current`'s
fingerprint. Another renderer version, a body the schema refuses or a failed
digest or fingerprint check is `incompatible`, which the hosted page words as
needing a compatible snapshot. A network failure, an HTTP error status, a
timeout of ten seconds per sibling or any redirect is `unavailable`. A body
whose tracker parent is not the record's is `other-epic`, with no stories. The
requests carry no `Authorization` or `Cookie` header, whatever the environment
holds, never use the publishing client, and follow no redirect. A link is read
over HTTPS, or from `http://127.0.0.1:<port>`, a loopback test receiver.

A body the service calls stale reads as `stale`, unless it is a confirmed
terminal cut: the last of `delivery.closed`, `lane.assigned`,
`candidate.handed` and `check.started` in its journal is `delivery.closed`, and
`current`, read again, names no newer cut. That reads as `read` with
`terminal: true`, as the viewer retains a completed delivery, so a closed
sibling's last stories still land.

### What the line map shows of a shared record

A shared record, one with `meta.collab`, gets two chapters, made the first time
its record is shared. A record nobody shares draws exactly as before. Both read
the record's journal and `meta.collab`, and no label decides anything.

- **Seats**, after Who. Who writes the record, each seat by its latest label:
  whether it endorses the plan, writes without endorsing, or waits to be
  admitted. Who holds each scope: `delivery`, `queue`, each lane, and each plan
  section passed on, with every other plan section the `delivery` holder's. A
  transfer counts only when the scope's holder made it. A queue handed to
  another seat says "queue moving to <label>" until that seat writes an entry
  that has seen the handover. Each peer's freshness, as the sync loop last read
  it: "<label> as of 4s", "<label> held: generation differs" or "held: its
  updates don't fit this record". A split is the record's own, never a peer's:
  a checkout whose seat split says so at the chapter's head.
- **Feedback**, before What happened, once any is raised. A thread per section
  it is on, in the order first raised: each feedback's writer and text, and
  whether it is open, open and blocking, or settled, with its outcome, who
  settled it and why.

A planning record's plan chapter keeps the preview's line: "Endorsed by
<labels>" once every approver seat endorsed its commit, and each endorsement,
current or stale, before that. Like any preview's, it says no form of
"approve".

On a shared record, "you" is the seat that holds the call. The local page is
drawn for its checkout's seat, which the renderer embeds; the hosted page has
none. A Needs you item is yours only when it is a person's call, its pass
stamped it with your seat, and the journal still gives that seat the item's
scope: only then does it take Now's sentence, mark "· you" and rank with your
calls. Every other seat's person's call reads under its holder's label, quieter,
and names that seat on its mark ("Merge · Ben"). The page words nothing itself:
a row reads the pass's `said` on its own seat's page, `saidAll` with no seat,
and `saidRole` when its seat is unknown or the journal has since given its
scope to another seat; the summary is the pass's `summary`, or `summaryAll`
with no seat. The rejoin's person words, the merge and the call to accept, are
the delivery holder's. Under the headline the local page says whose it is,
"<label> · holds delivery · lane 2" and "Shared with Ben", and the hosted page
says "Shared by Ama and Ben". Every other "the lead" stays as written, since
Seats already says who holds what.

### What the views show of siblings

The state wrapper words what the record's siblings show once, as the
document's `across`, and the line map, the Markdown view and the built-in page
print those words. A record with no siblings draws exactly as before.

- **Across repositories.** One group per sibling, folded shut: its name, linked
  to its live view, its stories at the stages its last kept read showed, and a
  time. A sibling's `readAt` is the last read that changed it, and
  `lastAttemptAt` is when its latest outcome began. A fresh sibling says "no
  change since <readAt>"; any other says its outcome "since <lastAttemptAt>":
  "couldn't be read", "stale", "needs a compatible snapshot" or "delivers
  another epic". A sibling no read has reached says "never read". A sibling of
  another epic shows no stories. The line map adds the chapter after What's
  left, the Markdown view and the built-in page after Planned. In the line
  map's chapter a sibling of another epic sits outside the fold, under an "Also
  linked" row after the epic's own groups, with its outcome, and the chapter's
  title counts only the epic's own.
- **The epic's post.** With a same-epic sibling, the line map's main line runs
  on past this repository's "Finish line" post, which keeps its place at the end
  of the ticks, to a second post for the epic. Its label, "Epic", and its count, "L/T
  landed" across this repository's pieces and every same-epic sibling's
  stories, stack above it. At most two siblings get a row under the lanes, with
  their name and "L of T landed" and no stops, and turn in at the epic's post
  from below: first those holding a story this repository waits on, then the
  furthest behind, by the lowest share landed, then the most open. The rest
  fold into a "+N more" chip by the post, whose accessible name lists them. A
  sibling's turn is solid once it has stories and all of them have landed, and
  dashed before, a fresh sibling with no stories included. The epic's line and post are solid only once every piece here and
  every sibling story has landed and every sibling is fresh.
- **An unreadable sibling.** A same-epic sibling that isn't fresh turns in
  dotted, and its row reads "Never read" or "Couldn't be read since <at>". The
  epic's line and post go dashed, the label reads "Epic ?", and under it goes
  the sibling's name, or "N can't be read". Every stroke of the epic's post is
  the neutral edge colour, never a warning colour. The drawing's accessible
  name adds the epic's count across its repositories, each sibling that can't
  be read and how many are folded. A sibling of another epic draws nothing on
  the line and counts for nothing there. With no same-epic sibling the line is
  drawn exactly as before.
- **The done count.** Until every one of this delivery's own stories has landed
  it is unchanged. Once they have, a same-epic sibling with open stories in its
  last kept read, one never read, or one not fresh adds its sentence, and the
  views say "This repository's part is done." with those sentences in place of
  a done count: "<name> has N open." ("..., couldn't be read since <at>."
  when it isn't fresh), "<name> couldn't be read (never read).", or "<name>
  couldn't be read since <at>.". The line map shows the first as Now's sentence,
  and the siblings' sentences below it; the Markdown view and the built-in page
  say them as they are. Every
  same-epic sibling fresh with everything landed leaves the usual done count.
  The delivery still closes by itself.
- **Elsewhere.** An outside child a drawn sibling's stories hold appears in that
  sibling's group; the rest stay under "Elsewhere in KEY", which the Markdown
  view and the built-in page now draw too.

### Installed archive qualification

The publisher, public projection and snapshot schema ship together in every
profile selecting delivery-views, including run-delivery and tracker profiles.
Use the supported archive lifecycle; never copy scripts into an adopter or edit
its installed generation. Each repository configures its own repository ID and
publishing-token environment name. Matching local delivery names retain separate
hosted identities. Preserve private identity/receipt sidecars with their record
for archive viewing and same-link recovery; they are not reusable release files.

Qualification uses two clean adopters and verifies their exact built archive and
unchanged installed bytes. A local receiver or socket substitution remains a
fixture seam even when the installed publisher and request child execute unchanged.
Source tests, installed-archive qualification, service CI, published-candidate
adoption and actual remote acceptance are separate evidence. Only the last proves
off-machine progress and disconnect/catch-up. The deployable viewer and its Node
dependencies never enter portable workflow archives.
