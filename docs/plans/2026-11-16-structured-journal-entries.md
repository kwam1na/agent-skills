---
reviewSlug: structured-journal-entries
---

# Give journal entries the facts a view needs, as fields

A delivery's journal entry is `{id, at, kind, lane, subject, summary}`. Everything else an entry knows is inside
`summary`, an English sentence a tool wrote. The live page's timeline now words each entry itself, and to do so it
matches regular expressions against those sentences: which round, which verdicts, how many checks and why, which
reason stopped a piece, and whether a `decision.recorded` is the lead's decision or a file grant. A reworded sentence
makes the match fail, and the page falls back to the raw text without saying so.

This plan adds the facts to the entry as fields. The tools write them beside the sentence, and a view reads them
first. No sentence changes.

Checked against `main` at `7e63d59` on 2026-11-16, which has `linear-v2.4.0` installed. The inventory this plan rests
on was taken from that commit: three scripts write entries, all through `delivery_state.append_journal`, at about 60
write sites, in 32 kinds, with 25 distinct phrasings of `decision.recorded`.

## Finish line

- Both plan lenses align.
- Every entry a tool writes carries the fields the tables below name for its kind, and the state tool refuses an
  entry whose fields do not fit its kind.
- The live page's timeline words an entry from its fields when it has them, and words an entry without them exactly
  as it does before this delivery.
- No `summary` a tool writes changes by one character, and no entry written before this delivery is refused.
- The work reaches `main` through a reviewed change request. Before it does, the full local suite passes on macOS
  with Python 3.11 at the integration candidate, run with `--workers 4 --budget tests/suite-budget.json`, and the
  suite stays inside the budget.
- CI's Ubuntu and `cut-release` jobs pass for that push to `main`.
- A candidate release holding the change is applied in this repository and runs one delivery's record before the
  final version is tagged, as `AGENTS.md` requires for a record-shape change.

## What exists today

- `delivery-journal/1` (`skills/orchestrate-work/references/schemas.json`) sets `additionalProperties` to false. Its
  optional members are `review`, `occurredAt`, `recovery`, `baseMergeAuthorization`, `reviewCarry` and
  `reviewArtifact`, each written by one path and checked by `journal_errors`.
- `review.round` already carries its facts as a field: `review` holds the round, its verdicts by lens and its
  findings by severity. The page does not read it; it parses the sentence.
- A stopped candidate's reason is one of 34 codes, the `reason` enum of `merge-queue-result/1`, written as the
  sentence's prefix (`check_failed: …`). One stop, `interrupted: …`, uses a word that is not in that enum.
- The lead may journal any kind by hand through `delivery_state.py journal`, with a sentence and nothing else.
  `lease.granted` and `lease.released` are only ever written that way.
- Twelve places in the Python tools read their own sentences back, by prefix, suffix or pattern, for example
  `repaired_state` on `base_broken:` and the supervisor on `fullHealth failed`.
- The page's field-less wording lives in `feedStory`, `feedQuiet`, `feedRows` and `batchNow` in
  `skills/delivery-views/assets/line-map.js`. The first three are on the unmerged branch `claude/live-view-ui`.

## Decisions

The operator approves these with the plan.

1. One new optional member, `data`, on `delivery-journal/1`: an object of facts about the entry. It is added to
   `/1`, as `reviewCarry` and `reviewArtifact` were, so the journal's version does not change.
2. `summary` stays required and stays the entry's human reading. This delivery changes no sentence. A view that does
   not know `data` loses nothing.
3. `data` is checked by kind. The schema names, for each kind, the members its `data` may hold, and refuses any
   other. An entry of a kind with no table below may not carry `data`. An entry may always omit `data`, so an entry
   the lead journals by hand, and every entry written before this delivery, stays valid.
4. Field names mean one thing everywhere: `head` is a full commit id, `base` is `{ref, head}`, `changeRequest` is the
   forge's address, `checks` is a list of catalogue ids, `reason` is a code from the `merge-queue-result/1` enum,
   `detail` is that code's free text, `log` is a path under the record's `logs`, and `routine` is `true` or absent.
   A commit is always written whole, never cut to ten characters as some sentences cut it.
5. What a tool did, when the kind does not say, is named by `data.act`, a closed list. It applies to
   `decision.recorded` and `blocker.recorded`, the two kinds that carry many meanings. No new kind is added.
6. Bookkeeping is marked where it is written, with `data.routine: true`. The acts that are routine are fixed by this
   plan's table and by the schema, which refuses `routine` on any other act. A view shows or folds routine entries
   without knowing the list.
7. A batch names its members: each `check.started` and `check.finished` of a batch carries `data.batch`, the
   subjects in order. A view no longer compares sentences to find a batch.
8. `review.round` keeps `review` as its facts and gains only what `review` lacks: `data.refused` and `data.restarts`,
   written when either is above zero.
9. The page reads `data` first and falls back, entry by entry, to today's matching when an entry has none. The
   fallback stays until a later delivery removes it; this one adds no date for that.
10. New tests add no fixture and no new process. A field is asserted in a case that already produces its entry. The
    suite ran at 1,702s of 1,740s and its slowest unit at 391s of 450s on 2026-11-15, so there is no room for
    another build.
11. The release is minor: nothing an adopter has configured changes meaning. It goes out as a candidate first.

On Decision 5, the alternative was a kind for each act, such as `ownership.granted`. It reads more truthfully in the
raw journal, since a file grant is not a decision. It was not chosen because every reader that switches on kind would
have to learn about 20 new kinds at once: the page's tabs and labels, the tracker publisher, `lead-wait`, the host
hooks and the tests that pin the kind list. An older page would show each as an unknown kind. A member inside `data`
changes nothing for a reader that does not ask for it.

## The fields, by kind

`data` members for each kind. A member marked `?` is written only when it applies.

| Kind | Written by | `data` |
| --- | --- | --- |
| `lane.assigned` | `lead-assign` | `title`, `note?` |
| `review.round` | `review-reduce` | `refused?`, `restarts?` |
| `review.evidence` | `capture-review` | `routine` |
| `candidate.handed` | `handoff` | `head`, `changeRequest`, `rounds`, `checks`, `waiting`, `evidence?` as `{check, result, head}`, `noChange?` as `{baseHead, settled}` |
| `check.selected` | `select` | `head`, `checks`, `why`, `added` as a list of `{id, why}`; or `cleared` alone |
| `check.started`, `check.finished` | the queue | `batch?` |
| `check.failed` | the queue | `check`, `timedOut`, `failing`, `more`, `log` |
| `check.retried` | the queue | `check`, `failing`, `flake`, `onBase` |
| `check.reused` | the queue | `check`, `head`, `from` |
| `check.carried` | the queue | `checks`, `ranAgain`, `base`, `commits` |
| `candidate.merged` | the queue, `reconcile` | `head` |
| `candidate.stopped` | the queue | `reason`, `detail`, `requeued?`, `stage?` |
| `selection.refused` | the queue | `detail` |
| `base.rerun.started` | the queue | `checks`, `base` |
| `base.rerun.finished` | the queue | `base`, `seconds`, `verdict`, `results` as a list of `{check, outcome, log?}` |
| `base.rerun.stopped` | the queue | `base`, `seconds`, `why` |
| `base.broken` | the queue, `fullHealth` | `source`, `reason?`, `detail?`, `head?`, `after?` as `{subject, entry}` |
| `base.repaired`, `base.verified`, `base.flaky` | the queue, `fullHealth` | `source`, `head`, `after?`, `failing?`, `repair?` |
| `base.moved` | `base-move` | `from`, `to`, `merged`, `reason` |
| `release.started`, `release.finished` | the queue | `head`, `failedAt?` |
| `finish.met` | the supervisor | `finish` |
| `hook.held` | the host hook | `hook`, `verdict` |
| `peer.merged` | the supervisor | `peer`, `subject`, `head`, `files` |
| `delivery.closed` | the close-out | `skipped?` as `{posture, source}` |
| `blocker.recorded` | the queue, the supervisor | `act`, and that act's members |
| `decision.recorded` | several | `act`, and that act's members |

`verdict` is `base_broken`, `candidate` or `none`. `outcome` is `passed`, `passed_after_retry`, `failed` or
`unjudged`. `source` is `queue` or `fullHealth`. For `candidate.stopped`, `reason` is a code from the
`merge-queue-result/1` enum or `interrupted`, which Decision 4 adds as the one value outside it.

The acts, with what each carries:

| `act` | Kind | Written by | Members | Routine |
| --- | --- | --- | --- | --- |
| `record.started` | decision | `lead-init` | `base` | |
| `story.added` | decision | `lead-plan`, `lead-init` | | |
| `plan.updated` | decision | every command that rebuilds the plan | `updated`, `changed` | yes |
| `ownership.granted`, `ownership.revoked` | decision | `lead-own` | `path`, `span?` as `[start, end]`, `base?`, `movedFrom?` | yes |
| `ownership.claimed` | decision | `lead-own --claim` | `path`, `base` | yes |
| `base.published` | decision | `lead-push` | `base`, `from` | yes |
| `handoff.checked` | decision | `lead-accept` | `head`, `rounds`, `afterMerge`, `noChange`, `notes` as a list of sentences | |
| `handoff.accepted` | decision | `handoff --accept` | `settled` | |
| `handoff.rejected` | decision | `handoff --reject` | `why` | |
| `handoff.requeued` | decision | `lead-accept --requeue` | `head` | |
| `review.carried` | decision | `carry-review` | `why` | |
| `base-merge.authorized` | decision | `lead-authorize-base-merge` | | |
| `cleanup.done` | decision | `lead-cleanup`, the supervisor | `base`, `changeRequest?`, `head?` | yes |
| `followup.filed` | decision | the close-out | `finding`, `followup`, `title` | yes |
| `evidence.published` | decision | `lead-evidence` | `url`, `clips` | |
| `flake.declared` | decision | `lead-flake` | `flake`, `check`, `head` | |
| `fullhealth.skipped` | decision | the close-out | `head`, `posture`, `source`, `why` | |
| `fullhealth.rerun` | decision | `lead-resolve` | `head`, `why` | |
| `posture.changed` | decision | the close-out | `from`, `to`, `source`, `why` | |
| `blocker.resolved` | decision | `lead-resolve` | `entry`, `why` | |
| `snapshot.stale`, `snapshot.retaken` | decision | `tracker-snapshot`, `forge-snapshot` | `source` | yes |
| `serving.held` | blocker | the queue | `why` | |
| `deferral.breached` | blocker | the supervisor | `finding`, `title` | |

`review.carried` and `base-merge.authorized` add no members because `reviewCarry` and `baseMergeAuthorization`
already hold their facts.

## Out of scope

- Rewording any `summary`.
- Moving the Python tools off reading their own sentences. The twelve places stay as they are; with the fields
  written, a later delivery can change each to read a field, with its own tests.
- Removing the page's fallback matching.
- New kinds, a new journal version, or changing which kinds exist.
- The tracker publisher and the Markdown view, which post and print `summary` and keep doing so.
- The delivery-history page, which reads kind, id and `reviewArtifact` only.
- This repository's own binding and settings. No story edits `.agents/` or `catalog.json`; the lead writes catalog
  and profile wording once, at integration.

## Approval Handoffs

- The operator approves this plan and its Decisions before any story starts.
- The operator approves the integration change request into `main`.
- The operator approves tagging the candidate, and separately the final version after the candidate has run one
  delivery's record in this repository.

## Execution posture and sensors

Test-first for the writers and the schema: each field is asserted before it is written. Characterization-first for
the page: its wording of field-less entries is pinned as it stands before the field path is added, so the fallback
is proven unchanged.

Sensors, as `AGENTS.md` names them:

- `python3 -B scripts/validate-registry.py` and `python3 -B scripts/update-provenance.py --check`, on every story.
- Each story's own test modules, through `scripts/run-tests.py`.
- `tests.test_orchestration_state` on every story that edits the orchestration guide or a skill page, since it
  checks every file name they mention.
- `tests.test_delivery_views_page` for the page story, which runs the page scripts under Node.
- The full suite once, at the integration candidate, inside `tests/suite-budget.json`.

Shared resources: none new. No story adds a port, a fixed path or a serial unit. Every story's tests compare to
nothing on the merge base.

Extension pins to move with the schema change, found by searching the tests for literals:

- `tests/test_orchestration_state.py` pins the journal schema's members and several exact sentences. The story that
  adds `data` updates the member pins and leaves every sentence pin green, which is the proof that no sentence moved.
- `tests/fixtures/delivery-views/line-map/source/journal.jsonl` and the goldens built from it stay field-less on
  purpose: they are the fallback's characterization. The page story adds its field cases beside them.

## Scenarios

- An entry written with `data` that fits its kind is accepted, and one with a member its kind does not name, or with
  `data` on a kind that has no table, is refused with the member named and nothing appended.
- `routine` on an act the table does not mark is refused.
- A journal written by `linear-v2.4.0`, with no `data` anywhere, validates and renders as it does today.
- Each sentence a tool writes is byte-for-byte what it was, with its fields beside it.
- A batch of three journals three `check.started` entries whose `data.batch` lists the same three subjects in order.
- A lane's round with one refused attempt carries `review` and `data.refused: 1`.
- The page words a `candidate.stopped` with `data.reason: "base_broken"` from the field when the sentence is changed
  to something its patterns would not match.
- The page folds an entry with `data.routine: true` into the bookkeeping row whatever its sentence says, and does
  not fold a hand-written `decision.recorded` that happens to begin "granted".
- An entry with an `act` the page does not know is shown by its sentence, not hidden.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | JF-1, JF-2 | `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_state.py`, `tests/test_merge_occurrence.py` |
| 2 | 2 | JF-3 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_merge_queue.py` |
| 2 | 3 | JF-4 | `skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_peers.py`, `tests/test_run_delivery_hooks.py` |
| 3 | 2 | JF-5 | `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/references/`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |

The keys are placeholders. Tracker items are filed once the plan is approved, each with its acceptance criteria
below, and the headings take their keys then.

JF-1 writes the whole schema, for every kind and act in the tables, so no later story edits `schemas.json`. Lanes 2
and 3 start when JF-1 has merged. No file is shared between lanes.

## Stories

### JF-1. Let a journal entry carry facts as fields, checked by its kind

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/SKILL.md` and `tests/test_orchestration_state.py`.

Add `data` to `delivery-journal/1` by Decisions 1 and 3 to 6, with the per-kind and per-act members of the tables.
`append_journal` takes it, `journal_errors` checks it, and the `journal` command takes `--data` as JSON, so a lead or
an adopter's own tool can write fields too. The guide gains one section: the field names and what each means, the
tables, and the rule that a view reads fields first.

Before writing, characterize what the installed `linear-v2.4.0` does with a journal that holds an entry carrying
`data`: its state tool's `show`, and its page. Report the result in the hand-off. If the older state tool refuses
such a record, say whether the record's writer pin already keeps the two apart.

Acceptance criteria:

- `c1` An entry whose `data` fits its kind's table is appended; one that does not is refused, naming the member, and
  nothing is appended.
- `c2` An entry with no `data`, of any kind, is valid, and a journal written before this change validates.
- `c3` `routine` is accepted only on the acts the table marks.
- `c4` `journal --data` writes the same entry `append_journal(data=…)` does, and a malformed value is a usage error.
- `c5` The guide lists every field name with its meaning, and every kind's and act's members.

### JF-2. Write the state tool's own entries with their fields

Lane 1, tier 1, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`tests/test_orchestration_state.py` and `tests/test_merge_occurrence.py`.

The state tool writes twelve entries itself: both hand-offs, the acceptance and the rejection, the check selection
and its clearing, the review record, the carried review, the recovered merge, the two snapshot notes and
`base.moved`. Each gains its row's fields. `base.moved` is built by hand in `complete_base_move_locked` and is
changed there.

Acceptance criteria:

- `c1` Each of the twelve entries carries its table row's members, with full commit ids.
- `c2` Each one's sentence is unchanged, shown by the existing sentence pins staying green untouched.
- `c3` `review.evidence` and both snapshot notes carry `routine`.

### JF-3. Write the merge queue's entries with their fields

Lane 2, tier 2, after JF-1. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py` and
`tests/test_merge_queue.py`.

`Runner.journal` takes `data` and passes it on, and every one of the queue's write sites supplies its row's fields,
the held-serving blocker included, which calls `append_journal` itself. A
stopped candidate's `reason` and `detail` come from the `Stop` the sentence is already built from, so the field and
the prefix cannot disagree. A batch's entries carry `batch`. The rerun's verdict and each check's outcome are written
from the values that choose the sentence's wording, not parsed back out of it.

Acceptance criteria:

- `c1` Every entry the queue writes carries its table row's members.
- `c2` `candidate.stopped` and the queue's `base.broken` carry the same `reason` their sentence starts with, for
  every code the existing cases reach, and the interrupted stop carries `interrupted`.
- `c3` A batch's `check.started` and `check.finished` list its subjects in order in `batch`; a single candidate's
  carry none.
- `c4` `base.rerun.finished` carries `verdict` and a result for each check it reran.
- `c5` No sentence changes.

### JF-4. Write run-delivery's entries with their fields, and mark its bookkeeping

Lane 3, tier 2, after JF-1. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_peers.py` and
`tests/test_run_delivery_hooks.py`.

run-delivery writes its entries through `state_tool("journal", …)`, and one, the host hook's, by running the state
tool directly. Each passes `--data`. Most are `decision.recorded`, and each takes its `act` from the table. The
ownership, publish, plan-update, clean-up and follow-up entries carry `routine`. The supervisor's `fullHealth`
entries carry `source: "fullHealth"`, the head and the merge they followed.

Acceptance criteria:

- `c1` Every entry run-delivery writes carries its table row's members, and every `decision.recorded` and
  `blocker.recorded` it writes carries an `act`.
- `c2` The acts the table marks routine carry `routine`, and no other entry does.
- `c3` `handoff.checked` carries each decision the lead made at acceptance as one sentence in `notes`, in the order
  the sentence lists them.
- `c4` No sentence changes.

### JF-5. Word the timeline from an entry's fields, and from its sentence only when it has none

Lane 2, tier 3, after JF-2, JF-3 and JF-4. Characterization-first. Files:
`skills/delivery-views/assets/line-map.js`, `skills/delivery-views/references/`, `tests/delivery_views_page/` and
`tests/fixtures/delivery-views/`.

Depends on the timeline rework on `claude/live-view-ui` having reached `main`, since `feedStory`, `feedQuiet` and
`feedRows` are that branch's. First pin how the page words each field-less kind today, from the existing fixture.
Then make each of those functions, and `batchNow`, read `data` first: the headline from `kind` and `act`, the detail
from the fields, bookkeeping from `routine`, a batch from `batch`, a round from `review`. An entry without `data`
takes the existing path unchanged.

Acceptance criteria:

- `c1` For each kind and act in the tables, an entry with fields is worded from them, shown by changing its sentence
  to one the patterns would not match and getting the same row.
- `c2` An entry without `data` is worded exactly as before this story, shown by the characterization cases staying
  green untouched.
- `c3` Bookkeeping is folded by `routine` alone when the entry has `data`.
- `c4` A batch is found from `batch` when present, in both the timeline and the checking sheet.
- `c5` An entry whose `act` the page does not know is shown with its sentence.

## Open questions for the operator

1. Should the Python tools stop reading their own sentences in this delivery? This plan leaves the twelve places
   alone to keep the change additive. Including them roughly doubles the work and touches the queue's stop and repair
   paths.
2. Is `interrupted` acceptable as a `reason` outside the queue's enum, or should the enum gain it? Adding it there
   changes `merge-queue-result/1` too.
3. Should `data` be open for an adopter's own kinds, so a repository's tool can journal fields this schema does not
   name? This plan refuses unknown members, which keeps the vocabulary one thing and means an adopter's tool cannot
   extend it.
4. The timeline rework is unmerged. Should it land on `main` first as its own change, or should JF-5 carry it?
5. Suite headroom is 38 seconds overall and 59 on the slowest unit. If asserting fields in existing cases still
   pushes a unit over, the choice is to make other tests cheaper in the same story or to raise the budget, which
   only the operator may do.
