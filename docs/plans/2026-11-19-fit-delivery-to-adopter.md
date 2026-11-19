---
reviewSlug: fit-delivery-to-adopter
---

# Fit a delivery to the adopter's tracker workflow, review lenses and git hooks

A delivery assumes, in three places, that its adopter works the way this repository does. A real adopter on Jira hit
all three. Its team refines tickets before starting them, and readiness refuses exactly the stories that are refined.
It wants two broad lenses only on the stories that call for them, and nothing stops a lane running every lens. And its
pre-commit hook fails, or rewrites the tool's own files, whenever run-delivery commits in the lead's checkout. This
epic, V26-3656, makes readiness and the publisher agree, lets the plan's approval refine the stories, puts a story lens
on request, and keeps the tool's own commits away from repository hooks.

The epic is delivered on its own integration base, `integrate/fit-to-adopter`. The lead then takes it into `main` as
the guide's "Reviewing an integration base for the default branch" says.

Checked against `main` at `aeeeafce` on 2026-11-19:

- `story_readiness` (`skills/run-delivery/scripts/run_delivery.py`) returns ready for an item whose snapshot `state`
  isn't `backlog`. For a `backlog` item, it is ready only when `start[0]` is among `offeredTransitions`. It never reads
  `targets`. `lead-assign` (through `readiness` at its call) and `doctor --delivery`'s readiness row both use it.
- The tracker snapshot (`skills/delivery-views/scripts/snapshot.py`) maps Jira's `new` status category, and Linear's
  `triage`, `backlog` and `unstarted` types, to `backlog`. So on both trackers, a ready status and a backlog status
  share the state `backlog`.
- The config reference's own example is `"start": ["Refined", "Start work"]` with `targets` naming only `Complete`.
- `TrackerPublisher.move` (`skills/run-delivery/scripts/tracker_publisher.py`) reads a move as made when its target
  (from the offered moves, else `targets`) is the ticket's status. It makes a configured list of moves for one journal
  entry on one ticket. `moves` maps `lane.assigned` to `start`, and other kinds to `transitions.lead[kind]`.
  `tracker_conf` validates `start`, `review`, `reviewWhen`, `targets` and `lead`, and refuses a `lead` kind the journal
  doesn't have.
- `lead-init` journals `record.started` (posts on the epic) and nothing per story. A story added later through
  `lead-plan change --kind added`, or the registration in `lead-brief`, `lead-assign` and the sweep, journals
  `story.added` (posts on the epic).
- Each lens's use is `plan`, `story` or both (`LENS_USES`), set in an own-config `review.lenses` entry's `use` or in
  `review.use` for a `harness.config.ts` lens. `Config.story_lenses` is every lens whose use includes `story`.
  `chosen_lenses` checks `--lenses`, and `story_lens_set` reads the set fixed in round 1's meta. A round 1 with no
  `--lenses` runs every story lens. A delivery file's story entry carries no lens field.
- `lead-plan change` takes `--kind added|removed|relaxed|changed|finish`, and refuses a flag its kind doesn't use.
- run-delivery commits with plain `git commit` in three places: `lead_plan` for a plan change, `lead_plan` for an
  approval, and `lead_sync` for the prepared base-move merge, which `git merge --no-ff --no-commit` builds first. The
  merge queue already runs its git with `-c core.hooksPath=<an empty folder>` under `hooks: "bypass"`.

## Finish line

- Both plan lenses align.
- V26-3654, V26-3655, V26-3653 and V26-3651 have landed on `integrate/fit-to-adopter` through the merge queue.
- The epic's five acceptance criteria hold on the integration base. Each is shown by a test named in Scenarios.
- The lead has taken the integration base into `main` by the guide's recipe: `main` merged in, the full local suite
  passed on macOS with Python 3.11 at the integration head with `--workers 4 --budget tests/suite-budget.json` and
  inside the budget, the integration review aligned, and the change request merged by the operator. This is a
  lead-held line: it names no criteria and no proofs.
- The epic goes out as one candidate release, is applied in the Jira adopter, and is then promoted. Tagging is the
  operator's and not this delivery's. The record gains a kind and optional fields: a minor version.

## Decisions

1. **The tool's own commits skip repository hooks** (V26-3654). One helper, `tool_commit`, runs every git command that
   creates a commit for run-delivery itself with `-c core.hooksPath=<an empty folder>`, as the merge queue does. The
   folder is made under the git common directory's `run-delivery/` folder and stays empty. It covers `lead_plan`'s two
   commits, and `lead_sync`'s merge and commit. It applies whatever the binding's `hooks` says.
   - This skips every hook, commit-msg and secret scanning included, not only formatters. That is right for these
     commits: the tool writes the delivery file and the plan itself, and a sync merge brings in commits that already
     passed their authors' hooks. The config reference says so in its "Host hooks" neighbourhood, by name.
   - A corpus test fails when `run_delivery.py` holds a `git("commit"` or a `git merge` that builds a commit outside
     `tool_commit`.
   - Nothing is journalled for it. The ticket asked for a journal record, but the skip is unconditional, so a field
     would always say the same thing, and the commits it covers have no journal entry of their own to carry it.
2. **Readiness agrees with the publisher** (V26-3655). `story_readiness` walks `start` as `move` does. A leading move
   counts as made when its target, from the item's offered moves or from `targets`, is the item's `status`. The story
   is ready when the item offers the first move not yet made. A `backlog` item with every move made is ready. The
   refusal names the item's status and that first move: `"<key> is in <status>: \"<move>\" isn't offered; refine it
   first"`. A non-`backlog` item is ready, as today. Without `targets`, nothing changes.
3. **`transitions.refine`** (V26-3653). An optional list of the adopter's move names, in `tracker.transitions`.
   `tracker_conf` validates it as it validates `start`, and requires a `targets` entry for its last move, so a move
   already made reads as made on a tracker that lists only the moves out of a status. The tooling assumes no names,
   statuses or step count.
4. **`story.approved` carries the refine moves** (V26-3653). A new kind, posted on the story's ticket, with data
   `{title, plan, review?}`: the plan's path and its review record's path, as the delivery file names them. Its
   tracker update says the plan holding this story was approved for delivery and names both paths.
   - `lead-init` journals one per planned story, in plan order, after `record.started`, only when `refine` is set.
   - A story added later journals one after its `story.added`, only when `refine` is set.
   - `moves` maps `story.approved` to `refine`. `move` already records an item at the target as made, and an item the
     move isn't offered from as `handled`, with "belongs to people" in the update. Nothing new is needed there.
   - Without `refine`, nothing journals it, so nothing is posted.
   - One entry per story keeps the publisher's rule of one entry, one ticket, and its markers and ledger as they are.
     Fanning one `record.started` out to every story would need both to learn a ticket per action.
5. **Readiness under `refine`** (V26-3653). A `backlog` item is also ready when it offers `refine[0]`, or its first
   refine move not yet made, since approval will make it. `lead-init` prints, per story, whether approval will refine
   it, finds it refined already, or can't move it, from the snapshot. `doctor --delivery`'s readiness row says the same
   before init.
6. **A lens on request** (V26-3651). An own-config `review.lenses` entry may carry `"default": false`, and
   `review.default`, a map of lens id to `false`, does the same for a `harness.config.ts` lens, as `review.use` gives
   one its use. Either makes the lens a story lens outside the default set. `true` is the same as leaving it out. A
   default on a mandated lens, a plan-only lens, or an undeclared one is a config conflict, which `doctor` and every
   command report. Two declarations that disagree are a conflict, as `use` is.
7. **The default set** (V26-3651). A round 1 with no `--lenses` runs the mandated pair, every story lens not on
   request, and every lens the story's brief names. With no lens on request and no brief lenses, that is every story
   lens, as today. `--lenses` still accepts any story lens, on request or not.
8. **The brief asks for a lens** (V26-3651). A delivery file's story entry may carry `lenses`, a list of declared
   story lens ids. `lead-setup` drafts it from a `Lenses:` sentence in the story's plan section, as it drafts files
   from `Files:`. It is part of the plan: frozen at `lead-init`, and after that changed only by
   `lead-plan change --kind lenses --subjects KEY --lenses IDS --text WHY`, which records the change and commits the
   delivery file, as other plan changes do. `--lenses` with an empty list removes the field.
9. **A brief lens can't be dropped** (V26-3651). A round 1 `--lenses` that leaves out a lens the story's entry names
   is refused, naming the lens and that the story's brief asks for it, as a missing mandated lens is.
10. **Where a lens came from** (V26-3651). Round 1's meta gains `requested`: each on-request lens in the set, mapped to
    `lane` or `brief`. A lens both the brief and `--lenses` name is `brief`. `handoff`'s message, `lead-accept`'s
    lens line and `lead-brief`'s lens list show it. `review.started` is unchanged: its `lenses` already holds the
    story's set, and `journal-kinds.json` closes that entry's data.
11. **Listing on-request lenses** (V26-3651). `doctor`, `config` and `lead-brief` list the on-request lenses on a line
    of their own, each with its charter's purpose, as `charter_purpose` reads it.
12. **The written paths agree.** `skills/run-delivery/references/config.md` describes `refine`, `story.approved` and
    the lens `default` with neutral example names. `skills/run-delivery/references/adopting.md` says when an adopter
    sets `refine`. `skills/run-delivery/SKILL.md`'s rows for `review-prepare` and `lead-plan` say what changed.

## Out of scope

- Formatting a delivery's changes, and checking that it happened (V26-3652). It waits for the checks design
  (V26-3645).
- Running repository hooks, or skipping them, for commits a lane or the lead makes with plain git.
- Refining on any trigger other than the plan's approval.
- A per-delivery default lens set beyond the story entries.

## Approval Handoffs

- The operator approves this plan before `lead-init`.
- The operator granted the lanes `push`, `draftPullRequests` and `readyPullRequests` on 2026-11-19.
- The operator merges the integration change request into `main`, and tags the candidate and then the final release.

## Execution posture and sensors

Test-first for every story.

Sensors:
- `tests/test_run_delivery_tool_commits.py` (new, V26-3654).
- `tests/test_run_delivery_tracker.py` (V26-3655, V26-3653), which already drives the publisher against a fake
  adapter.
- `tests/test_run_delivery_lens_request.py` (new, V26-3651).
- `tests/test_run_delivery.py`, whose readiness and lens tests these stories extend.
- `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/vectors.json` for
  `story.approved`.
- `tests/test_own_deliveries.py`, which holds every test module to the binding's catalogue.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.

Each new module is catalogued in `.agents/orchestration.json` in the same diff, as `docs/own-deliveries.md` says.
The new modules reuse the fixtures the existing run-delivery modules build, through a `SharedCorpus` per module. They
add no class that runs alone and keep the suite inside `tests/suite-budget.json`. A module that would push the run over
its budget makes other tests in the same change cheaper; no budget number moves.

## Scenarios

`tests/test_run_delivery_tool_commits.py`:
- With a `pre-commit` hook that exits 1 installed through `core.hooksPath`, `lead-plan change --kind added`, `lead-plan
  approval <id> --state done` and a `lead-sync --merge` each commit, and the hook never ran.
- With a `pre-commit` hook that rewrites a staged file, the same three commits hold exactly the bytes the tool wrote.
- A `commit-msg` hook that exits 1 doesn't stop them either.
- A plain `git commit` in the same checkout still runs the failing hook and fails, so the setting never leaks into
  the repository's configuration.
- The corpus guard: a `git("commit"` added outside `tool_commit` fails it, shown by a fixture source.

`tests/test_run_delivery_tracker.py`:
- V26-3655, each with made-up names `Groom` and `Begin`, `start: ["Groom", "Begin"]` and `targets: {"Groom":
  "Groomed"}`:
  - a Jira-shaped snapshot item in `Groomed`, offering only `Begin`: ready;
  - the same with a Linear-shaped item: ready;
  - an item in `Backlog` offering `Groom`: ready, as today;
  - an item in `Backlog` offering neither: refused, naming `Backlog` and `Groom`;
  - an item in `Groomed` offering nothing: refused, naming `Groomed` and `Begin`;
  - `start: ["Begin"]` alone behaves as it does today in each of these.
- V26-3653, with `refine: ["Groom"]` (one step) and with `refine: ["Triage", "Groom"]` (two steps), `targets` naming
  each:
  - `lead-init` journals one `story.approved` per planned story, in plan order, after `record.started`;
  - the publisher makes the refine moves once on each story that needs them, with an update naming the plan and its
    review record; a second run makes none;
  - a story already at the last refine target isn't moved and is recorded as made;
  - a story in a status the move isn't offered from is left alone, recorded `handled`, with "belongs to people" in its
    update;
  - a story added with `lead-plan change --kind added` after init journals `story.approved` and is refined the same
    way;
  - readiness under `refine` accepts a story offering `Groom`, and one already `Groomed`, and refuses one offering
    neither, naming its status;
  - `lead-init` prints each story's refine outcome, and `doctor --delivery` prints the same before init;
  - without `refine`, `lead-init` journals no `story.approved` and the publisher posts and moves exactly what it did.
- `tracker_conf` refuses a `refine` that isn't a list of names, and one whose last move has no `targets` entry.

`tests/test_run_delivery_lens_request.py`, with lenses `lens.standards` and `lens.data` declared for story use and
`lens.data` on request:
- A round 1 with no `--lenses` runs the mandated pair and `lens.standards`, not `lens.data`, and the meta's
  `requested` is empty.
- `--lenses` naming `lens.data` runs it, with `requested: {"lens.data": "lane"}`.
- A story whose entry names `lens.data` gets it with no `--lenses`, with `requested: {"lens.data": "brief"}`.
- That story's `--lenses` leaving out `lens.data` is refused, naming it and the brief.
- The same through `review.default` for a `harness.config.ts` lens.
- Conflicts: `default: false` on a mandated lens, on a plan-only lens and on an undeclared one, and `review.lenses`
  and `review.default` disagreeing, are each reported by `doctor`.
- With nothing on request and no brief lenses, a round 1 with no `--lenses` runs exactly what it runs on the base.
- `handoff` and `lead-accept` show `lens.data` as asked for by the brief. `lead-brief`, `doctor` and `config` list
  `lens.data` as on request with its charter's purpose.
- `lead-setup` drafts `lenses` from a `Lenses:` sentence. `lead-plan change --kind lenses` after init changes it,
  commits, and records the change; `--lenses` on another kind is refused by name.

`tests/fixtures/journal-kinds/vectors.json`:
- `story.approved` with and without `review`; one with no `plan`, rejected.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3654, V26-3655, V26-3653 | `skills/run-delivery/scripts/tracker_publisher.py`, `skills/orchestrate-work/references/journal-kinds.json`, `tests/fixtures/journal-kinds/vectors.json`, `tests/test_run_delivery_tool_commits.py`, `tests/test_run_delivery_tracker.py` |
| 1 | 2 | V26-3651 | `tests/test_run_delivery_lens_request.py` |

V26-3655 and V26-3653 both change `story_readiness`, so they share a lane, V26-3655 first. V26-3654 goes first in
that lane: it is small and touches `lead_plan`, which the other two stories also change.

The two lanes share these files. The lead grants each lane disjoint regions in them with `lead-own --region --around`
against the integration base's head at `lead-init`:

- `run_delivery.py`
  - Lane 1: `tool_commit` beside `def git(`; the commit lines in `lead_plan` and `lead_sync`; `story_readiness`;
    `lead_init`'s journal and print block; `story_added`'s callers' journal lines; `Doctor.story_readiness`.
  - Lane 2: `Config`'s lens use block through `self.plan_lenses_declared`; `story_lens_set` through `lenses_text`;
    `review_prepare`'s meta; `handoff`'s and `lead_accept`'s lens lines; `lead_brief`'s lens list; `lead_plan`'s
    `takes` map and a `lenses` branch; `draft_delivery`'s story drafting; the `lead-plan` parser's `--kind` and
    `--lenses`; the doctor's lens row.
- `skills/run-delivery/references/config.md`: lane 1 the `tracker.transitions` block and "Host hooks"; lane 2
  `review.lenses` and `review.use`.
- `skills/run-delivery/references/adopting.md`: lane 1 the tracker section; lane 2 the review section.
- `skills/run-delivery/SKILL.md`: lane 1 the `lead-plan` row; lane 2 the `review-prepare` row. Lane 2 asks the lead
  for a span on the `lead-plan` row's `--kind` list once lane 1's V26-3654 has merged.
- `.agents/orchestration.json`: each lane a span after `tests.test_run_delivery_gaps`'s catalogue entry for its new
  module's entry.

Both lanes refresh digests in `catalog.json`, `profiles/*` and `provenance.lock.json` as the repository's rules say
for a releasable skill change. Those are regenerable files under the listing guard. Neither lane adds or removes a
listed output.

If a change breaks an existing assertion in `tests/test_run_delivery.py` or `tests/test_run_delivery_supervisor.py`,
which no lane owns, the lane asks the lead for a region over that assertion and fixes it in the same change. An
assertion is never left red or deleted.

No ports, browser sessions or databases are shared. Both lanes' fixtures run in their own temporary folders.

## Stories

### V26-3654. Commit run-delivery's own files without running the repository's git hooks

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md` and `tests/test_run_delivery_tool_commits.py`.

Implement Decision 1.

### V26-3655. Read a story as ready when its status is already a start move's target

Lane 1, tier 1, after V26-3654. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py` and
`tests/test_run_delivery_tracker.py`.

Implement Decision 2.

### V26-3653. Refine a delivery's stories in the tracker when its plan is approved

Lane 1, tier 1, after V26-3655. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/orchestrate-work/references/journal-kinds.json`,
`tests/fixtures/journal-kinds/vectors.json`, `skills/run-delivery/references/config.md`,
`skills/run-delivery/references/adopting.md` and `tests/test_run_delivery_tracker.py`.

Implement Decisions 3 to 5, and Decision 12's tracker parts.

### V26-3651. Let a story lens be available but run only when named

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/config.md`,
`skills/run-delivery/references/adopting.md` and `tests/test_run_delivery_lens_request.py`.

Implement Decisions 6 to 11, and Decision 12's lens parts.
