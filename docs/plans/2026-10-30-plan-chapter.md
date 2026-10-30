---
title: "feat: Show a delivery's plan in the live view, bound to its record"
type: feat
status: approved
date: 2026-10-30
execution_posture: test-first
context: The live view says where a delivery is, but not whether the work is still what was agreed, or what proves it. A prototype built from a real 20-story delivery showed the chapter that answers that, and which of its parts were coupled to that one delivery.
---

# feat: Show a delivery's plan in the live view, bound to its record

## Outcome

The live view gains a chapter, **The plan**, that answers one question for the engineer working with the agents: is
the work still what we agreed, and what proves it?

- **Attention first.** By default the chapter shows only what an engineer acts on while the agents work:
  - how many of the plan's criteria are proven;
  - the outcome, in one sentence;
  - what needs them, or a plain "Nothing in the plan needs you right now";
  - the units not yet proven, each with its next missing proof;
  - one row of finish-line meters.
- **Folded until needed:**
  - every unit and its proofs, with an "as approved / as amended" switch;
  - every change since approval, as a scope-over-time chart and redlined criterion text;
  - waived proofs with their reasons;
  - approvals still ahead;
  - what is out of scope;
  - the full outcome.
- **Now** gains one line naming what is being built and how much is proven.
- **Each stop on The line** opens its unit in the chapter.
- **It stays current on a live page.** Record-derived proofs redraw with every pushed record, as The line does, and a
  change to the plan itself tells the viewer to reload.

**The view is agnostic.** It reads one new input, `delivery-plan/1`, and the record it already reads. It knows nothing
about any repository, tracker, plan format or kind of evidence beyond what that input declares.
- `run-delivery` produces the input.
- A repository that writes it any other way gets the same chapter, and a command checks its file.
- A delivery without it gets today's page.

## Current-State Evidence

**The live view today.**
- The `delivery-views` line map has six chapters: Now, The line, Who, What's left, What happened and Parked. All six
  report status.
- None of them says what the delivery committed to, whether that changed, or which evidence proves which criterion.
- The record's finish lines (`state.finish[{id, text, met}]`) appear as a list under What's left and as Now's journey
  stop.

**What the first adopter's delivery showed** (20 stories, 9 lanes, archived record and plan history):
- **The plan changed 7 times after approval:**
  - 6 stories were added: three pipeline-review follow-ups, two operator requests and one lane finding;
  - one acceptance criterion was relaxed by the operator;
  - one in-app check was moved after a lane showed it proved nothing;
  - one finish line was added.

  None of it was visible in the live view.
- **A finish line drifted:** it still named "AC1–AC14" when the plan had 29 criteria, so it could have been marked met
  without the 15 added later. Nothing flagged it.
- **Two in-app checks were waived,** with written reasons that only hand-off files held.

**The prototype** (private) bound that plan to that record.
- **Version 1 showed everything.** The operator found it text-heavy and overwhelming, and asked for only what an
  engineer needs while working with the agents, with the rest collapsed.
- **Version 2, attention-first, read clearly.**
- **The operator rejected "promises"** as the word.
- **It was coupled to that one delivery:**
  - parsing plan headings and the criterion table;
  - hand-written change records, approvals and finish-line mappings;
  - a regex for "AC1–AC14";
  - proof columns fixed to that repository's in-app evidence;
  - a fixed time zone;
  - deriving proofs once at render, so a live page went stale.

  This plan removes every one of those.

**Profiles and qualification.**
- `delivery-views` ships in the `delivery-views`, `linear` and `run-delivery` profiles.
- `core` and `bootstrap` carry no delivery-views or run-delivery file.
- Provider qualification qualifies the `core` profile.

## Execution Posture

- **`test-first`:** the input contract and validator (P1), the renderer (P2), the page's derivation, flags and folds
  (P3), and the producer (P4).
- **`characterization-first`:** the existing line-map page tests and goldens. Every one passes unchanged with no plan
  input, before and after.
- **`sensor-only`:** the skill text and the profile and catalog descriptions (P5).

## Plan Review Status

The outcome-correctness and adversarial lenses aligned after 4 rounds, on 2026-10-30.
- Round 1 raised 41 findings.
- Rounds 2 and 3 raised further findings against the revised text.
- In round 4 both lenses approved, with one non-blocking wording fix, adopted as written.

The evidence is in `2026-10-30-plan-chapter.review.json`. There is no round bound. The operator approved it on 2026-10-30,
with C12-A.

## Scope

### In scope

1. **The input.** `delivery-plan/1`: its schema, shipped in `delivery-views`; a standard-library validator; and
   `views_common.py check-plan`, so any producer can check its file.
2. **The renderer** reads, validates, bounds and embeds the input.
3. **The page:**
   - the chapter;
   - the line in Now;
   - the stop-to-unit link;
   - the rail entry;
   - finish-line meters in place of What's left's list, when a plan is present;
   - live derivation from each pushed record;
   - the reload notice.
4. **The producer.** `run-delivery` produces the input under one writer, from its delivery file, a durable changes
   log, and its existing flow logs and acceptance records.
5. **Tests:**
   - the contract and the renderer;
   - page behaviour and goldens for two synthetic fixture deliveries, one with in-app proofs and one with record
     proofs only;
   - every existing test unchanged with no plan;
   - the producer's commands.

### Out of scope

- Parsing a plan's prose or headings in the view.
- Changes to `orchestrate-work`'s schemas or to the live server's watch list. A plan write moves the record through an
  ordinary journal entry instead (C9).
- Editing the plan from the page.
- Mirroring changes to a tracker.
- The history page.

## Contract Decisions

**C1. Words.**
- A criterion is **proven**. A finish line is **met**, as the record already says.
- Inside the chapter, "met" is used only for finish lines.
- The title reads "N of M criteria proven", adding ", W by waiver" when any proof was waived.

**C2. The input, `delivery-plan/1`.** One JSON object, at most 1 MiB, refused unread if larger.

| Field | Contents |
| --- | --- |
| `schemaVersion` | `delivery-plan/1`. |
| `updated` | When the producer last wrote it. |
| `outcome` | `{summary, text}`: one sentence, and the full text. |
| `requiredLenses` | Lens ids every approving review round must include. |
| `proofKinds` | Ordered `{id, label, source}`, with `source` either `record` or `producer`. Columns follow this order. A delivery declares only the kinds it uses. |
| `units` | Ordered `{subject, label?, title, removed?, criteria: [{id, text}], proofs: [kind ids owed]}`. |
| `finish` | Ordered `{id, label, text, at, covers: {criteria: [ids] \| "all" \| [], proofs: [kind ids]}}`. `id` equals the record's finish line id. `at` is when the line was last written. `[]` means the line covers no criteria by design. |
| `approved` | `{at, review?, snapshot: {units, finish}}`. The snapshot is frozen once at the start of the delivery and never rewritten. |
| `changes` | Ordered `{id, at, kind, subjects, criteria?, by, why?, title?, source?, ref?, before?, after?}`. `kind` is one of `added`, `removed`, `relaxed`, `changed` or `finish`. `before` and `after` hold a criterion's text, or a finish line's `covers`. `by` may be `unrecorded`. |
| `evidence` | `{<subject>: {<producer kind>: {state, head?, at?, reason?, items?}}}`. `state` is `done`, `todo` or `waived`. `done` and `waived` require `head`; `waived` also requires `reason`. `items` lists each pattern or check behind the kind. |
| `approvals` | Optional, ordered `{text, state: done \| waiting, who?}`. Absent hides the fold. |
| `outOfScope` | Optional list of texts. Absent hides the fold. |

- **Caps.**
  - Text: `outcome.text` 2,000 characters, other texts 600, labels 200.
  - Lists: 200 units, 1,000 criteria, 500 changes, 30 finish lines.
- **Ids** match `[A-Za-z0-9._-]{1,64}`, and criterion ids are unique.
- **Subjects** are those the record names, now or later. The view uses a subject only as data, never in a selector or
  as markup.
- **Refused:** unknown keys, and any record kind outside C4.
- **`ref`** renders as text. It becomes a link only through the view's existing configured-origin link rule.

**C3. Reading it.** The renderer reads `<state directory>/plan.json`.
- **Absent.**
  - No `delivery-plan` block, no rail entry, no Now line. What's left keeps its finish-line list.
  - Every existing golden's embedded blocks, page text and detail panels are unchanged.
  - No line of the existing page tests changes, other than the pins P3 lists for the plan-present case.
- **Invalid.**
  - The page renders as if the input were absent, plus the chapter and its rail entry with one line: "The plan couldn't
    be read".
  - That line names the failing key path without echoing values.
  - It counts as a flag.
- **The view never reads** the plan's markdown, a delivery file or a producer's logs.

**C4. Proofs from the record.** These are derived in the page from each pushed record document, by the same code path
The line uses.

| Kind | Done when | Otherwise |
| --- | --- | --- |
| `handed` | A queue item exists for the subject, its stage is not `stopped`, and it is not pending acceptance. | Open while stopped or pending. |
| `merged` | The queue item's stage is `merged`. | — |
| `review` | The unit's review source approves from every `requiredLenses` lens, at the queue item's `head`. See the source rules below. | **Stale:** approved for another head. **Open:** a round that hasn't approved. **Not recorded:** nothing recorded. |

- **The head compared is the queue item's `head`,** meaning the reviewed and handed head, never `syncedHead`. The runner
  moves `syncedHead` only after it admits the sync against the review, so a synced or merged unit keeps its proofs.
- **The review source** is the first of these that applies:
  - **A `runId`:** the run's latest round, with journal verdicts laid over it. A round that names a candidate is
    compared with `head`. One that doesn't follows the hand-off-window rule below.
  - **An available review observation** whose candidate equals the queue item's `head`: the observation's final
    outcome.
  - **Otherwise, the journal's `review.round` entries,** under the hand-off-window rule. This covers an observation the
    record marks `unavailable` because the runner synced the candidate onto a moved base.
  - **`reportedRounds` alone** carries no verdicts, so the review reads "not recorded".
- **The hand-off-window rule.** A round with no candidate counts only when it was recorded after the
  `candidate.handed` before the current one. A unit handed off only once counts every round.
- **"Stale"** appears only when a source positively shows approval for a different head. An `unavailable` observation on
  a synced or merged item is never stale by itself.
- **Merges.** A hand-off after a merge supersedes the merge, as the record already treats it. A `merged_unverified`
  stop shows `merged` as open until the record shows the merge.

**C5. Producer proofs.** These come from `evidence`.
- A missing entry is `todo`.
- Once the subject has a queue item, a `done` or `waived` entry whose `head` differs from that item's `head` shows as
  **stale**, and counts as not done.
- Before hand-off, an entry counts at face value, and the open row says "before hand-off".

**C6. Definitions.**
- **A unit is proven** when every proof it owes is `done` or `waived` and none is stale.
- **A criterion is proven** when its unit is.
- **Removed units** are excluded from N and M.
- **A finish line's meter** fills by the share of covered criteria whose units have every `covers.proofs` kind done.
  A second segment adds the covered criteria whose units have those kinds done or waived, so the meter and the title
  agree.
  - `"all"` covers every criterion.
  - `[]` covers proofs only, over every unit.
  - The line's met state comes only from the record's finish line with the same id.
- **Record finish lines with no plan entry** still render as meters, from the record alone, with no fill.

**C7. What needs the engineer.** The attention area shows these flags, in this order. With none, it reads "Nothing in
the plan needs you right now."
1. **The plan couldn't be read.**
2. **The base is broken:** the last base event in the record is `base.broken`. While it holds, the title can't read
   "Every criterion is proven".
3. **Work in the record isn't in the plan:** a queue or planned subject has no unit. The flag names the subjects.
4. **The plan and the record disagree on finish lines:** a finish line in the plan that the record lacks, or the
   reverse.
5. **A finish line no longer covers the plan.** This applies only when its `covers.criteria` is an explicit, non-empty
   list that omits a criterion added or changed after its `at`. Lines with `"all"` or `[]` never raise it.
6. **A change touches open work:** a `relaxed`, `changed` or `removed` change names a unit that is neither proven nor
   removed. It clears once that unit's review approves at a time after the change.
7. **A change has no reason:** its `why` is absent.
8. **A waived proof on a unit not yet proven.**

Each rule is part of the documented contract and has a page test.

**C8. Placement and folding.**
- **Position.** The chapter sits between What's left and What happened: after the work ahead, before the history. The
  rail gains "The plan".
- **Open by default:**
  - the title (C1);
  - the outcome's summary;
  - the attention area;
  - the units not yet proven, each with its next missing proof;
  - the meter row.

  A page test asserts nothing else renders unfolded.
- **Folded, each with a count:**
  - all units;
  - changes since approval;
  - waived proofs;
  - approvals ahead;
  - out of scope;
  - the full outcome.
- **Fold memory.** Open folds are remembered per viewer under the page's existing theme-key prefix, and the page works
  without storage.
- **Now** gains the outcome's summary and a link: "N of M criteria proven · plan changed K times".
- **The line's stops** link to their unit: in the open list, or by opening the all-units fold.
- **What's left.** With a valid plan, its finish-line list gives way to the meters, which show every record line.

**C9. Live behaviour.**
- **Embedding.** The page embeds the validated plan once, and derives record proofs from each pushed document.
- **Plan writes move the record.**
  - The producer writes `plan.json` only when its content, apart from `updated`, differs from the last build.
  - It writes the file atomically first.
  - Then it appends a `decision.recorded` entry whose summary starts "Plan updated:", names the new `updated` value,
    and says what changed. That moves the record, so the live server re-renders the page from the new file.
- **Reload notice.** An open page shows "The plan changed. Reload to see it." beside the title when the latest
  "Plan updated:" entry in a pushed document names an `updated` value different from its embedded plan's. A page
  rendered or reloaded after the change shows no notice.

**C10. Presentation.**
- **Times** show in the viewer's time zone. Page tests fix the time zone and clock, as the existing ones do.
- **Text.** All text is set as text. Redlines are computed in the page from `before` and `after`.
- **Lookups.** Units are found through an internal map keyed by subject, never through a selector built from one.
- **Columns.** The grid's column count follows `proofKinds`. At phone width each unit becomes a card, with its proofs as
  labelled pills.
- **Budget.** When a plan is present, it gets a fixed 512 KiB share, taken from the runs budget; with no plan, the
  budget is unchanged.
  - Over its share, the oldest changes' text is clipped first.
  - The approved snapshot and every count are never dropped.
  - The page says what was clipped.

**C11. The producer, `run-delivery`.**
- **Optional.**
  - A delivery file without criteria writes no `plan.json`, so the delivery gets today's page.
  - `doctor` reports that case as `skipped (no criteria declared; the plan chapter is off)`, not as missing.
  - The delivery file's `schemaVersion` stays frozen, and every new key is optional.
- **Sources.** `plan.json` is a pure function of:
  - the delivery file at the base: `stories[].criteria`, `finish[].covers`, `.label` and `.at`, `outcome`,
    `approvals`, `outOfScope` and `requiredLenses`;
  - an append-only `plan-changes.jsonl` beside the lead's flow logs, archived by `lead-close`;
  - the existing app-check and video flow logs;
  - the acceptance records.
- **One writer.**
  - Only lead-side commands and the reconcile sweep rebuild `plan.json`, under one lock, written atomically.
  - Lanes only append their flow logs, as today.
  - The file is never read back as a source.
- **`lead-init`** writes the approved snapshot as the changes log's first entry. It copies finish lines into the record
  as `{id, text, met}` only, because the record's finish items accept no other keys.
- **A delivery started before this feature** has no snapshot. The first rebuild that finds criteria and no snapshot
  writes one, marked "taken after the delivery started" with its time, and the page says so on the switch.
- **Changes.**
  - **`lead-plan change --kind K --subjects ... [--criterion ID --text NEW | --covers JSON] --why W [--by B] [--ref R]`**
    records `before` and `after`, edits the delivery file in the lead's checkout of the delivery base, and commits it
    there.
    - It refuses on a dirty checkout or one that has diverged from `<remote>/<base>`.
    - It never pushes. It prints the push command for the lead, as the lead pushes other tooling edits today. Lanes see
      the change once pushed.
    - With `--kind finish`, it also patches the record's `finish` through the state tool, with `{id, text, met: false}`,
      so a finish line added mid-delivery can be met.
  - **`lead-plan why <change-id> --why W`** adds a reason later.
  - **Mid-delivery stories.** Wherever one is registered (`lead-brief`, the sweep, `lead-assign`), an `added` change
    is recorded with `why` absent. Flag 7 shows until a reason is given.
  - **Unrecorded edits.**
    - A rebuild appends a baseline entry to `plan-changes.jsonl`, holding the delivery file's digest, criteria and
      covers, only when that digest differs from the last baseline. The next rebuild compares against the last
      baseline, so `plan.json` is never read back.
    - A difference that a command-recorded change after that baseline explains is not counted.
    - Any other difference becomes a change with `by: "unrecorded"`.
  - **`removed`** drops the story from the delivery file, and removes or parks its planned entry through the state
    tool. The unit stays in the snapshot, marked removed.
- **Kinds declared.**
  - `review`, `handed` and `merged` from the record.
  - One producer kind per in-app mode the delivery uses, aggregating all of that mode's required checks and patterns
    into `items`.
  - `clip`, when any story is marked for video evidence.
  - A producer kind is `done` when every required item is current at the queue head. It is `waived` when every
    remaining item was accepted with `--accept-blocked`, with their reasons joined.

**C12. Sequencing, decided by the operator at approval.**
- **A: add these units to the run-delivery delivery, before its U5.**
  - Qualification is earned once, in U5.
  - It amends that delivery's approved plan. Its Out of scope excludes delivery-views changes, so the amendment lifts
    that line and widens U5 and its finish line. The amendment needs its own plan review and the operator's approval.
  - P4 and P5 wait until U4 merges, since U4 owns `run-delivery`'s guide and skill text.
  - It delays that release by these units.
- **B: a separate change after the run-delivery release.**
  - It restales the `linear` set, whose re-earn needs the live host attestation, and the `run-delivery` product's
    descriptor.
  - `core`, `bootstrap` and `provider` stay valid, since they carry no delivery-views or run-delivery file.
- **Recommended: A.** The live attestation is the costly step, and A does it once.

## Implementation Units

### P1. The input contract and validator

**Ownership:**
- `skills/delivery-views/references/delivery-plan.schema.json`;
- the validator and `check-plan` in `views_common.py`;
- `tests/test_delivery_views_plan.py`;
- the new output in the `delivery-views`, `linear` and `run-delivery` profiles' `expectedFiles`, and the catalog entry;
- the hand-added `provenance.lock.json` entry, then a run of the provenance writer;
- the release-integration pin on delivery-views files.

**Required scenarios:**
- A valid fixture is accepted.
- Each of these is refused with its key path named and no values echoed:
  - an unknown key;
  - an overlong text;
  - an over-count list;
  - a file over 1 MiB;
  - a duplicate criterion id;
  - an unknown record kind;
  - a `covers` naming an unknown criterion;
  - a `done` entry without `head`;
  - a `waived` entry without `reason`.
- `check-plan` passes on a valid file, and fails with the same messages on an invalid one.

### P2. The renderer

**Ownership:** `render.py`'s line-map path and its tests.

**Required scenarios:**
- With no `plan.json`, the C3 no-change guarantee holds against every existing golden.
- With an invalid `plan.json`, the page renders as if it were absent, plus the one-line chapter.
- An oversized plan stays within its share, clipped per C10, and says so.

### P3. The page

**Ownership:**
- `line-map.js` and `line-map.css`;
- the page tests;
- `golden.test.mjs`'s case list;
- `support.mjs`, whose `renderPage` places a `plan.json`;
- the rail-label pin in `line-map.test.mjs`;
- two synthetic fixture deliveries under `tests/fixtures/delivery-views/line-map/`.

**Approach:** characterize first.

**Required scenarios.** These are page tests with a fixed time zone, a fixed clock and empty storage.
- **Default content:** per C8, with nothing else unfolded.
- **Flags:** each of C7's eight, plus "nothing needs you", including one finish line covering no criteria and one
  covering `"all"`.
- **C4 shapes:**
  - a re-hand-off after a merge;
  - a stopped candidate;
  - a pending acceptance;
  - a batch landing;
  - `merged_unverified`;
  - a run-backed unit;
  - a native-review unit;
  - an approval at a stale head, using a journal-only fixture;
  - a synced candidate whose base moved, which keeps its review and producer proofs `done`;
  - a native-review unit with captured evidence, synced onto a moved base and then merged, which stays proven;
  - a merged unit, which is proven;
  - a unit with no recorded review;
  - a one-lens round where two lenses are required.
- **Stale producer evidence** after a re-hand-off.
- **Live updates:**
  - a pushed merge redraws the chapter;
  - a pushed "Plan updated:" entry naming a new `updated` shows the reload notice;
  - a page rendered after that entry shows none.
- **The switch:** as approved and as amended, including a removed unit.
- **Folds:** each fold, remembered without storage.
- **The stop-to-unit link,** for open and proven units.
- **The phone layout,** and both themes.
- **Escaping:** a subject, criterion and `why` containing `</script>`, `"` and an `<img onerror>`, and a `javascript:`
  ref.
- **Real record shapes.** The record documents for the synced and merged shapes come from `delivery_state.py show`, run
  on a record whose runner sync produced a new `syncedHead`. They are not written by hand.
- **Goldens** for both fixtures, one with three record columns only. The first-adopter term scan extends to these
  fixtures.

### P4. The producer

**Ownership:**
- `run_delivery.py`:
  - the optional delivery-file keys;
  - `lead-plan change` and `lead-plan why`;
  - change recording on registration;
  - drift detection;
  - the `lead-init` snapshot and record-finish stripping;
  - the plan build under one lock;
  - the "Plan updated:" journal entry;
- its tests;
- the `doctor` row;
- the U3 and U4 pins it moves: the doctor's row count and one-line-per-prerequisite tests, the guide's command list,
  and the fixture adopter's blocks.

**Dependencies:** P1, and under C12-A, U4 merged.

**Required scenarios:**
- `lead-init` succeeds with `covers` and `label` present, and the record holds only `{id, text, met}`.
- `lead-plan change` records `before` and `after`, edits and commits the delivery file, prints the push command, and
  refuses on a dirty or diverged checkout.
- `--kind finish` adds the line to the record.
- Two sweeps with no change write nothing: no `plan.json`, no journal entry and no changes-log entry.
- A delivery initialized before criteria existed gets a snapshot marked "taken after the delivery started".
- A registered mid-delivery story records an `added` change, and `lead-plan why` completes it.
- A hand edit to a criterion appears as `unrecorded`.
- A lane's `app-check` or `app-video` changes the plan only through the next lead-side rebuild.
- Two concurrent rebuilds don't lose an update.
- Every written `plan.json` passes P1's validator.
- A delivery without criteria writes no plan, and `doctor` shows the `skipped` row.

### P5. Skill text and registry descriptions

**Ownership:**
- `delivery-views/SKILL.md`: the input, the chapter, and C4, C6, C7 and C9;
- `run-delivery`'s guide: writing criteria and recording changes;
- the description sentences in the three profiles and the catalog.

**Required scenarios:** pins on C7's rules and C2's field list.

## Dependency Graph

- **Order:**
  - P1 → P2 → P3;
  - P1 → P4, and under C12-A, only after U4 merges;
  - P5 follows P3 and P4.
- **Parallel:** P3 and P4 touch disjoint files.
- **Shared resources:**
  - `provenance.lock.json`, through the writer, per AGENTS.md;
  - the page-test lane's `node_modules`;
  - the serial test modules.
- **Qualification:** under C12-A, in that delivery's U5.

## Representative End-to-End Scenarios

1. **No plan input:** the C3 no-change guarantee.
2. **A synthetic delivery shaped like the first adopter's:**
   - 20 units, 29 criteria and 7 changes;
   - a finish line whose explicit list predates 15 added criteria;
   - one open unit carrying a `changed` change;
   - a waiver on a proven unit.

   The chapter shows exactly flags 5 and 6, the open unit with its next proof, and six folds with counts.
3. **A delivery without in-app checks:** three columns, and "Nothing in the plan needs you right now".
4. **Every criterion proven and the base healthy:** "Every criterion is proven", and no open list.
5. **A story added mid-delivery:** its unit appears with an `added` change and flag 7, until `lead-plan why`.
6. **A live page during a merge:** the chapter redraws. After `lead-plan change`, it shows the reload notice.

## Repository Sensors

- registry validation and the registry tests;
- the full suite through `scripts/run-tests.py`;
- the page-test lane (jsdom, Node 20 or newer);
- `update-provenance.py --check`;
- `views_common.py check-plan` on the fixtures;
- under C12-A, that delivery's U5 qualification and release sensors.

## Risks and Mitigations

- **Clutter returns:** C8 fixes what is open by default, and a page test enforces it.
- **The producer and the view drift apart:** P4 validates every file it writes with P1's validator.
- **A stale plan on a live page:** C9's derivation and reload notice.
- **Wrong proof states on real record shapes:** C4's rules, and P3's shape scenarios.
- **Lost or racing writes:** C11's single writer and append-only log.
- **Size:** C10's fixed share and caps, with a test at the limit.
- **Hostile producer text:** C2's and C10's text-only rendering, the internal lookup map, and the escaping tests.

## Approval Handoffs

1. This plan's approval after the lenses align, with the C12 choice.
2. Under C12-A, an amendment to `2026-10-29-run-delivery-skill.md`:
   - it lifts that plan's delivery-views exclusion;
   - it adds P1–P5 before U5;
   - it widens U5's ownership and finish line.

   The amendment gets its own plan-review round and the operator's approval.

## Observable Finish Line

1. **Two fixtures.** The live view renders the chapter for two unrelated synthetic fixture deliveries, per C7 and C8.
   Their goldens contain no first-adopter term.
2. **No plan.** The C3 no-change guarantee holds against every existing golden.
3. **A producer-driven delivery.** A fixture delivery driven through `run-delivery`'s commands yields a `plan.json`
   that passes `check-plan`. It includes a recorded change, a mid-delivery story and an unrecorded edit, and its
   chapter's folds are non-empty.
4. **A live page.** A served page redraws the chapter on a pushed merge, and shows the reload notice after
   `lead-plan change`.
5. **One qualification.** The release that carries this is qualified once, under the C12 choice.
