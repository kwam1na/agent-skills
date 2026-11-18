---
reviewSlug: epic-across-repositories
date: 2026-11-18
execution_posture: test-first
context: An epic split by codebase is delivered as one delivery per repository, and those deliveries can't see each other. This plan keeps one delivery per repository and lets each name its siblings, read their published snapshots, show the whole epic, and wait on a sibling's story.
---

# Deliver one epic across repositories, each repository running its own delivery

Many teams keep one epic per feature and split its children by codebase. Each repository then runs its own
delivery for its own children. Today those deliveries can't see each other. A real adopter delivered an epic's
backend children from one repository and its mobile children from another, and three things went wrong:

- Each delivery took in the other repository's children from the tracker, as unassigned building work that held its
  close (V26-3363).
- Neither record showed the whole epic. One said "done" while the other repository's work was still open (V26-3072).
- A mobile story that needed a backend story merged first had no way to say so. A plan's `after` keys only name
  stories in the same delivery.

The epic is [V26-3602](https://linear.app/v26-technologies/issue/V26-3602). Its stories are V26-3363, V26-3603,
V26-3072 and V26-3604, and V26-3605 is the follow-up for the hosted page.

Checked against `main` at `932f9d81` on 2026-11-18, which has `v3.1.0` applied. The survey this plan rests on was
taken there:

- **One delivery is bound to one repository at every layer.** The generation is installed per repository at
  `.agent-skills/current`. `.agents/orchestration.json` names one base remote and ref, one candidate command and one
  set of checks. A base's merge slot is a lock beside the repository's git common directory, and lanes are worktrees
  of the lead's repository. A hosted publishing token is tied to one repository ID, and a write to another
  repository's delivery is refused (AE5 in `docs/plans/2026-11-15-1521-feat-hosted-delivery-views-plan.md`).
- **The record takes in outside facts through snapshot sources.** `SNAPSHOT_SOURCES = ("tracker", "forge")` in
  `delivery_state.py`. The binding's `state` member names a command for each source. `serve` runs each command every
  `refreshSeconds` (180 by default), and `reconcile` applies what it read, refusing a snapshot older than
  `snapshotMaxAgeSeconds` (900 by default). This workflow ships no forge or tracker client of its own. The ready-made
  commands live in `delivery-views/scripts/snapshot.py` and the `run-delivery` adapters.
- **`waits` reads only the record.** Its docstring says "no clock, no network, no other file".
  `WAIT_CAUSES = ("order", "files", "lane", "claim", "slot", "review", "held")`. An `order` wait is raised only for
  an `after` key the record holds (`if key in items and key not in landed`), so an `after` key naming another
  repository's ticket is ignored without a word. `lead-plan change --after` refuses such a key ("isn't a story of"),
  and `lead-setup` copies `after` keys from the plan without checking them.
- **A sibling's published snapshot can be read with no credential.** The hosted Worker serves `GET /d/{id}/current`
  and `GET /d/{id}/snapshots/{digest}` to anyone holding the 64-hex public ID. `current` returns the receipt
  (`rendererVersion`, `digest`, `sourceFingerprint`, `epoch`, `sequence`) with `serverNow`, `sourceConfirmedAt` and
  `stale`. The trusted viewer (`hosted/delivery-viewer/src/view-store.ts`) reads `current`, then fetches the body by
  digest, checks its sha256, and validates it against the schema and the fingerprint. Anything else is `incompatible`.
- **The hosted body carries what a sibling view needs, and no more.** `hosted-delivery-snapshot/2` keeps
  `state.meta.trackerParent`, `state.meta.updatedAt`, `state.meta.closing`, `state.planned[]` and `state.queue[]`
  with `subject`, `title`, `stage` and `changeRequest`. Its journal keeps only each entry's identity and kind, so it
  carries no merge time.
- **The service and the publishing generation move together.** A service reading another snapshot version refuses
  the body (`docs/hosted-deliveries.md`, "Versions move together"). So two repositories on different generations can
  still read each other while both publish `/2`.

No knowledge library is configured in this repository's binding, so none was searched. Two solution notes bear on
the plan. In `docs/solutions/design-patterns/merge-occurrence-and-observation-2026-10-23.md`, a source time and an
observation time are kept apart, and Decision 6 follows it. In
`docs/solutions/best-practices/rehearsal-proves-only-what-its-stub-left-2026-11-03.md`, a rehearsal proves only what
its stub left in, and the finish line follows it.

## What a reader should be able to do

- **On either delivery's live view:** see every repository's stories for the epic, with where each stands, and open
  the other delivery's live view.
- **When one repository's part is done:** read that the other's part is still open and how much of it, never that
  the epic is done.
- **On a story that needs another repository's work:** see what it waits on, in which repository, and that the wait
  clears when that work lands.
- **When a sibling can't be read:** see that, and when it was last read, rather than stale stages shown as current.

## Finish line

- Both plan lenses align.
- V26-3363, V26-3603, V26-3072 and V26-3604 are merged to `main`, and the full suite passes locally on macOS with
  Python 3.11, as `AGENTS.md` requires before tagging.
- The first version-tagged release after the merge carries the changes. Each changed skill's catalog entry, every
  selected profile and the provenance output digests move in the same change.
- Two repositories, each with hosted publishing, install that release. Each runs one delivery under one epic, and
  each names the other as a sibling. One story in one delivery has an `after` key naming a story in the other. On
  each delivery's live view, both repositories' stories show. The waiting story says what it waits on, and the wait
  clears with no action from either lead when the other story merges. The proof names anything it stubs, and lists
  any step that has never run for real.

## Decisions

1. **Federation, not one delivery across repositories.** Each repository keeps its own delivery, generation, binding,
   merge queue and lanes. No lead, queue or lane spans repositories. Spanning them would change every layer the
   survey lists, and merges across repositories can't be atomic. It would also add a host-level mechanism that
   `AGENTS.md` keeps out without a separately approved unit.
2. **A sibling is named by its hosted link.** The lead adds one with
   `lead-sibling add --link https://<origin>/d/<64 hex> --name <label>` and removes it with
   `lead-sibling remove --name <label>`. Each change is journaled as `sibling.added` or `sibling.removed`. The record
   keeps `siblings: [{name, link, ...}]` beside `meta`.
   - The link must be HTTPS, with the same loopback exception `hosted.py` already allows for tests.
   - A record without `meta.trackerParent` refuses a sibling, because Decision 5 can't check one.
   - The local record isn't public, so holding the link there widens nothing. The hosted projection drops
     `siblings` (Decision 9).
   - There is no discovery. The service offers no index, by design. The lead gets a sibling's link from that
     delivery's `hosted.py status` and adds it on both sides.
3. **Siblings are a third snapshot source.** `SNAPSHOT_SOURCES` gains `siblings`, and the binding's `state` member
   gains `siblingSnapshot: {argv, timeoutSeconds?}`, run the way the other sources are. `delivery-views` ships the
   ready-made command: `hosted.py siblings --state-dir {stateDirectory}`. It prints `sibling-snapshot/1`, one entry
   per sibling the record names, and reads each sibling the way the trusted viewer does:
   - It reads `current`, then fetches the body by digest and checks its sha256, the schema and the fingerprint.
   - The entry is `{name, link, readAt, outcome, trackerParent?, updatedAt?, closing?, stale?, stories?}`.
   - `stories` holds `{subject, title, stage, changeRequest?}` from the body's `planned` and `queue`.
   - `outcome` is one of `read`, `unavailable` (network, HTTP or timeout), `incompatible` (another `rendererVersion`
     or schema, or a failed digest or fingerprint check) or `other-epic`.
   - It sends no credential and follows no redirect.

   `reconcile` applies the snapshot under its existing freshness gate, and `serve` refreshes it on `refreshSeconds`.
   `waits`, `next` and every view read siblings from the record only, so `waits` keeps its "no network" contract.
4. **Version skew is refused, never half-read.** The reader accepts exactly the hosted snapshot version that its own
   generation publishes, which is `/2` today. A sibling publishing another version reads as `incompatible` and is
   shown as needing a compatible snapshot, the hosted page's own words for that case. Two repositories on different
   generations can read each other as long as both publish the same snapshot version. The snapshot is the contract
   between them, not the runtime.
5. **A sibling must belong to the same epic.** If a read sibling's `trackerParent` differs from this record's, the
   outcome is `other-epic`. That sibling contributes no stories, and `reconcile` journals `sibling.mismatched` once
   for each sibling and parent pair.
6. **What the record keeps, and what never frees a wait.** `reconcile` keeps each sibling's last `read` result and
   adds `lastOutcome` and `lastAttemptAt` beside it. A read that is `unavailable`, `incompatible` or stale (the
   service's own `stale`, or `readAt` older than `snapshotMaxAgeSeconds`) never changes a sibling story's stage. It
   only changes what the views say about the sibling.
   - A sibling story reads landed when its stage is `merged`, or `settled` for a planned entry, the same test as
     `_landed_subjects`.
   - The first `read` showing a story landed journals `sibling.landed` once, with `subject`, `sibling` and
     `firstSeenAt`. That is an observation time: the hosted body carries no merge time, so the record never
     presents one as when the merge happened.
7. **Sibling reading is a `doctor` check.** `doctor` lists each sibling with its last outcome, and names a binding
   that has siblings in its record but no `siblingSnapshot` command.
8. **The combined view is a chapter, not a new page.** The live line map, the Markdown view and the built-in page
   gain an "Across repositories" chapter:
   - one group per sibling, with its name, a link to its live view, its stories with their stage words, and its
     last read;
   - a sibling that isn't `read` and fresh is drawn unavailable, with the reason and the time of its last good read;
   - the chapter is collapsed by default, as the "Elsewhere" section is.

   The headline changes only at close: when this delivery's own stories are done and a sibling still has open
   stories, every view says "This repository's part is done. <name> has N open." in place of a done headline. The
   delivery still closes by itself, because each repository closes its own part. A record with no siblings draws
   exactly as before.
9. **Local views only. The hosted page is unchanged.** This is the operator's call of 2026-11-18. The hosted
   projection drops `siblings` and the sibling journal kinds' facts, and `hosted-delivery-snapshot` stays at `/2`.
   Carrying siblings on the hosted page, and deciding whether a sibling's read link may appear on another delivery's
   public page, is V26-3605.
10. **"Elsewhere" and siblings meet.** V26-3363's outside children that a sibling holds, matched by `subject`, are
    drawn in that sibling's group with its stage and link. Outside children that no sibling holds stay in
    "Elsewhere" with their tracker status only. Without `--scope`, a sibling's stories that the tracker sweep would
    otherwise take in are already excluded by V26-3363.
11. **An `after` key may name a sibling's story.** `waits` gains the cause `sibling`, placed after `order` in
    `WAIT_CAUSES`. It is raised for an `after` key that no story of this delivery holds and that a sibling's stories
    hold unlanded. Its sentence in `wait-words.json` is "Waits for {on:ticket} to land in {sibling}. {where}", and
    `{where}` says "last read {at}" when the read is not fresh. `frees` is `landed`, and `needsSomeone` is false.
    - `next` treats the key as an open blocker until the sibling's story reads landed.
    - `delivery_state.py wait` wakes on `sibling.landed`.
    - `lead-plan change --after` accepts a key that a sibling's stories hold.
    - `lead-setup` notes, without refusing, an `after` key that is neither a story nor a known sibling story, since
      siblings may not have been read yet.
12. **An unresolved `after` key changes behaviour only in a record with siblings.** If a record has at least one
    sibling, an `after` key held by no story and no sibling raises a `sibling` wait whose sentence says no sibling
    lists it. That wait has `needsSomeone: true`, because the lead has to add the sibling or fix the key. In a record
    without siblings, such a key is ignored, as today. That pins today's behaviour for every existing record.
13. **Tracker blockers are unchanged.** The tracker's `blockedBy` and V26-3363's `--status-state` map remain how a
    tracker-only adopter orders across repositories. Where both the tracker and a sibling read speak, `next` treats
    the story as blocked until both say the blocker is done, as it already does for the plan's order beside
    `blockedBy`.

## Out of scope

- One lead, queue or lane set spanning repositories, and merges that are atomic across repositories (Decision 1).
- Carrying siblings on the hosted page (V26-3605).
- Discovering siblings automatically, or any index of deliveries on the hosted service.
- Reading a sibling from a local state directory or another checkout instead of its hosted link.
- V26-3363's own out-of-scope items: the Linear snapshot's `--scope`.
- Continuing a record past merge through deploy and QA (V26-3071).
- A sibling's checks, rounds or journal facts. A sibling contributes its stories' stages only.

## Approval Handoffs

- **Plan review:** both plan lenses align, or the operator ends the review by decision.
- **Operator, before the finish-line proof:** choose two repositories with hosted publishing, provisioned with
  distinct repository IDs and tokens, and name the shared epic.
- **Operator:** tag the release, after the full local run on macOS with Python 3.11.
- **Already given, 2026-11-18:** local views only (Decision 9), V26-3363 folded in as tier 1, and the epic's tickets
  created (V26-3602 to V26-3605).

## Execution posture and sensors

Test-first for V26-3603, V26-3072 and V26-3604. V26-3363 is test-first against its Jira fixture. V26-3604 also pins
today's ignored-key behaviour first, which is characterization-first for Decision 12.

Sensors, as `AGENTS.md` names them:

- `.venv/bin/python scripts/validate-registry.py`, on every candidate through the binding's `alwaysRun`.
- `.venv/bin/python scripts/run-tests.py --workers 4` for the full suite at merge. Before the tag, run the budgeted
  form with Python 3.11 on macOS.
- The page-behaviour lane (`tests/test_delivery_views_page.py`) for every line-map change.
- `.venv/bin/python scripts/update-provenance.py --check` before hand-off, for every story that changes a listed
  output.

Budget: the suite is already over its 1740s budget (V26-3595). These stories don't raise any number in
`tests/suite-budget.json`. A new test reuses its class's fixture, and the sibling reader's network tests share one
loopback receiver per class on port 0. None is marked `run_serially`.

Shared resources: each class's loopback receiver binds port 0, so no fixed port is shared. No story needs a browser
session beyond the page lane's `jsdom`.

Pins that move with each extension:

- `WAIT_CAUSES` and the `wait-words.json` marker test (V26-3604).
- `SNAPSHOT_SOURCES` and the binding schema in `schemas.json` (V26-3603).
- The journal-kinds registry with its vectors in `tests/fixtures/journal-kinds/vectors.json` and
  `tests/test_journal_kinds.py` (V26-3603: `sibling.added`, `sibling.removed`, `sibling.mismatched`,
  `sibling.landed`).
- The delivery-views goldens under `tests/fixtures/delivery-views/` (V26-3072, V26-3363).
- Each changed skill's `degradedBehavior` in `catalog.json`, its profiles, and `provenance.lock.json` (every story).

## Scenarios

V26-3363 keeps its own seven acceptance criteria as its scenarios.

V26-3603:

1. `lead-sibling add` on a record with `meta.trackerParent` journals `sibling.added`, and `remove` journals
   `sibling.removed`. On a record without `trackerParent`, `add` is refused and nothing is written. A non-HTTPS
   link other than loopback is refused.
2. Against a loopback receiver serving a valid `/2` body, `hosted.py siblings` prints `outcome: read` with the
   body's stories. `reconcile` applies it, and the record's sibling holds those stories and `readAt`.
3. A body whose sha256 doesn't match its digest, a `rendererVersion` of `/3`, or a fingerprint mismatch each read
   as `incompatible`. The record keeps its last `read` stories unchanged.
4. A receiver that times out or returns 500 reads as `unavailable`. The record keeps its last `read`, and
   `lastOutcome` says `unavailable`.
5. A sibling whose `trackerParent` differs reads as `other-epic`. It contributes no stories, and
   `sibling.mismatched` is journaled once across two reconciles.
6. A story first read `merged` journals `sibling.landed` once, with `firstSeenAt` equal to that `readAt`. A later
   read journals nothing more.
7. The hosted projection of a record with siblings has no `siblings` member and validates as `/2`.

V26-3072:

8. A record with two siblings, one `read` and one `unavailable`, draws an "Across repositories" chapter with both
   groups. The unavailable group shows its reason and the time of its last good read.
9. With every one of its own stories merged and a sibling holding two open stories, the headline on the line map,
   the Markdown view and the built-in page says "This repository's part is done. <name> has 2 open." The delivery
   still closes.
10. A V26-3363 outside child that a sibling holds is drawn in that sibling's group, and one no sibling holds stays
    in "Elsewhere".
11. A record with no siblings renders byte for byte as the current goldens.

V26-3604:

12. Characterization: in a record without siblings, an `after` key naming a ticket outside the record raises no
    wait, and `next` offers the story. This is pinned before any change.
13. A planned story `after` a sibling's `building` story shows "Waits for KEY to land in <sibling>. Building." and
    `next` doesn't offer it.
14. When a fresh read shows that story `merged`, the wait is gone, `next` offers the story, and `wait` returns on
    `sibling.landed`. No lead command ran.
15. With the sibling's last read stale and showing `building`, the wait stays and says when it was last read. A
    stale read showing `merged` doesn't clear it either, until a fresh read does.
16. In a record with a sibling, an `after` key held by no story and no sibling raises a wait with
    `needsSomeone: true`, and it shows in "Needs you".
17. `lead-plan change --after` accepts a sibling's story key and still refuses a key held by nothing. `lead-setup`
    notes a plan `after` key that is neither a story nor a known sibling story.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3363 | `skills/delivery-views/scripts/snapshot.py`, `tests/test_delivery_views_snapshots.py`, `tests/fixtures/delivery-views-snapshots/jira-scope/` |
| 1 | 2 | V26-3603 | `skills/delivery-views/scripts/hosted.py`, `tests/test_siblings.py`, `tests/fixtures/siblings/` |
| 2 | 1 | V26-3072 | `tests/delivery_views_page/line-map-siblings.test.mjs` |
| 2 | 2 | V26-3604 | `tests/test_waits_sibling.py` |

Shared by region, granted by the lead with `lead-own --region` when each story is assigned and revoked when it
lands:

- `skills/orchestrate-work/scripts/delivery_state.py` and `skills/orchestrate-work/scripts/render_state.py`
- `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json` and
  `skills/orchestrate-work/references/wait-words.json`
- `skills/orchestrate-work/references/orchestration-guide.md` and `skills/orchestrate-work/SKILL.md`
- `skills/run-delivery/scripts/run_delivery.py` and `skills/run-delivery/references/config.md`
- `skills/delivery-views/scripts/public_projection.py`, `skills/delivery-views/scripts/render.py`,
  `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css` and
  `skills/delivery-views/SKILL.md`
- `catalog.json` and `profiles/`
- `tests/fixtures/journal-kinds/vectors.json`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`,
  `tests/test_waits.py` and `tests/test_lead_wait.py`
- `tests/test_run_delivery.py`, `tests/test_orchestration_render.py`, `tests/test_delivery_views_page.py`,
  `tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted_projection.py` and the goldens under
  `tests/fixtures/delivery-views/`

`provenance.lock.json` is regenerable: each story takes the lock side that lists its new files and runs the writer,
as `AGENTS.md` describes.

Lanes 1 and 2 start at once. V26-3363 and V26-3603 touch different parts of `delivery_state.py`: the tracker sweep,
and the snapshot sources and `reconcile`. The lead grants each its region. V26-3072 needs both: it draws siblings
from V26-3603's record member and merges them with V26-3363's "Elsewhere" section. So lane 1 goes on to V26-3072
when both have landed. V26-3604 needs only V26-3603's record member and journal kinds, so lane 2 goes on to it when
V26-3603 lands.

## Stories

### V26-3363. Scope a delivery's tracker snapshot to its own repository's tickets, and map an adopter's statuses by name

Lane 1, tier 1, not blocked. Test-first. Files: `skills/delivery-views/scripts/snapshot.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/scripts/render.py`, `skills/delivery-views/SKILL.md`, `catalog.json`, `profiles/`,
`tests/test_delivery_views_snapshots.py`, `tests/fixtures/delivery-views-snapshots/jira-scope/`, `tests/test_run_delivery.py`,
`tests/test_delivery_views_page.py` and `tests/fixtures/delivery-views/`.

As the ticket proposes: `--scope` and `--status-state` on the Jira snapshot, the `outside` list, the sweep skipping
and clearing outside items, the doctor checks, and the collapsed "Elsewhere in KEY" section. Its acceptance criteria
are the ticket's seven.

### V26-3603. Read sibling deliveries' published snapshots into the record

Lane 2, tier 1, not blocked. Test-first. Files: `skills/delivery-views/scripts/hosted.py`,
`skills/delivery-views/scripts/public_projection.py`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/config.md`,
`skills/delivery-views/SKILL.md`, `catalog.json`, `profiles/`, `tests/test_siblings.py`, `tests/fixtures/siblings/`,
`tests/fixtures/journal-kinds/vectors.json`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`,
`tests/test_run_delivery.py` and `tests/test_delivery_views_hosted_projection.py`.

Add `lead-sibling` and the record's `siblings` by Decision 2. Add the `siblings` source and `hosted.py siblings` by
Decisions 3 and 4. Apply reads in `reconcile` by Decisions 5 and 6, add the doctor check by Decision 7, and drop
`siblings` from the hosted projection by Decision 9. Scenarios 1 to 7.

### V26-3072. Show one record for an epic delivered across repositories

Lane 1, tier 2, after V26-3363 and V26-3603. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/delivery-views/scripts/render.py`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/delivery-views/SKILL.md`, `catalog.json`, `profiles/`,
`tests/delivery_views_page/line-map-siblings.test.mjs`, `tests/test_delivery_views_page.py`,
`tests/test_delivery_views_render.py`, `tests/test_orchestration_render.py` and `tests/fixtures/delivery-views/`.

Draw the "Across repositories" chapter and the part-done headline by Decision 8, and merge "Elsewhere" with siblings
by Decision 10, on the line map, the Markdown view and the built-in page. Scenarios 8 to 11.

### V26-3604. Let a story wait on a sibling delivery's story landing

Lane 2, tier 2, after V26-3603. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/wait-words.json`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/delivery-views/assets/line-map.js`, `catalog.json`,
`profiles/`, `tests/test_waits_sibling.py`, `tests/test_waits.py`, `tests/test_lead_wait.py`,
`tests/test_run_delivery.py` and `tests/test_delivery_views_page.py`.

Pin today's ignored-key behaviour first (scenario 12). Then add the `sibling` cause, `next`'s blocker, `wait`'s
wake, and the `lead-plan` and `lead-setup` changes, by Decisions 11 to 13. Scenarios 12 to 17.

## Calls made

- Federation over one delivery spanning repositories: agreed with the operator on 2026-11-18, before planning.
- Local views only, with the hosted page as V26-3605: the operator, 2026-11-18.
- V26-3363 folded in as tier 1: the operator, 2026-11-18.
- Plan review runs unbounded, as `plan-work` records, and ends only on alignment or by the operator's decision.
