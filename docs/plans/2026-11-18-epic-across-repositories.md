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
  and `lead-setup` notes an `after` key the plan has no story for without refusing it.
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
- Each changed skill's catalog entry, every selected profile and the provenance output digests move in the same
  change as the skill.
- The plan changes the record's shape: a `siblings` member, four journal kinds and a wait cause. So the release goes
  out as `AGENTS.md` and `docs/product-generation.md` require for a record-shape change. A `-rc.N` candidate is
  tagged first, the two-repository proof below runs on that candidate as its adopter application, and the final is
  then tagged on the same commit. Each adopter verifies the final's own digests, which differ from the candidate's.
- Two repositories, each with hosted publishing, apply the candidate. Each runs one delivery under one epic, and each
  names the other as a sibling. One story in one delivery has an `after` key naming a story in the other. On each
  delivery's live view, both repositories' stories show. The waiting story says what it waits on, and the wait
  clears with no action from either lead when the other story merges, including when the other delivery has already
  closed. The proof names anything it stubs, and lists any step that has never run for real.

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
   - The entry is `{name, link, readAt, outcome, trackerParent?, updatedAt?, closing?, stale?, terminal?, stories?}`.
   - `stories` holds `{subject, title, stage, changeRequest?}` from the body's `planned` and `queue`. An entry carries
     `stories` only when its outcome is `read` or `stale`.
   - `outcome` is one of `read`, `stale`, `unavailable` (network, HTTP status, timeout or any redirect),
     `incompatible` (another `rendererVersion`, a body the schema refuses, or a failed digest or fingerprint check)
     or `other-epic`.
   - `terminal` is true when the body is a confirmed terminal cut, by the trusted viewer's own test: the last of
     `delivery.closed`, `lane.assigned`, `candidate.handed` and `check.started` in its journal is `delivery.closed`,
     and `current` names no newer pointer. A read is `stale` when the service says `stale` and the body is not
     terminal. A terminal body read stale is `read`, as the viewer retains a confirmed terminal cut rather than
     labelling it stale (`hosted/delivery-viewer/src/view-store.ts`). Without this, a sibling that closed would read
     stale for ever, and a wait on its last story would never clear.
   - It sends no credential. It does not reuse the publishing client in `hosted.py`, which always sends the
     repository's bearer token. Its requests carry no `Authorization` or `Cookie` header, whatever the environment
     holds. It follows no redirect: any 3xx reads as `unavailable`.

   `reconcile` applies the snapshot under its existing freshness gate, which refuses a sibling snapshot older than
   `snapshotMaxAgeSeconds`. Unlike a refused tracker or forge snapshot, which raises out of `reconcile` and writes
   nothing, a refused sibling snapshot is handled per entry (Decision 6). Tracker and forge snapshots in the same
   call still apply. `serve` refreshes it on `refreshSeconds`.
   `waits`, `next` and every view read siblings from the record only, so `waits` keeps its "no clock, no network"
   contract.
4. **Version skew is refused, never half-read.** The reader accepts exactly the hosted snapshot version that its own
   generation publishes, which is `/2` today. A sibling publishing another version reads as `incompatible` and is
   shown as needing a compatible snapshot, the hosted page's own words for that case. Two repositories on different
   generations can read each other as long as both publish the same snapshot version. The snapshot is the contract
   between them, not the runtime.
5. **A sibling must belong to the same epic.** If a read sibling's `trackerParent` differs from this record's, the
   outcome is `other-epic`. That sibling contributes no stories, and `reconcile` journals `sibling.mismatched` once
   for each sibling and parent pair.
6. **What the record keeps, and what never frees a wait.** Freshness is decided once, when `reconcile` applies a
   read, and is written into the record. Nothing downstream needs a clock.
   - For each sibling, the record keeps `stories` and `readAt` from its last `read` entry, and `lastOutcome` and
     `lastAttemptAt` from its latest entry of any outcome.
   - Only a `read` entry changes a sibling story's stage or journals anything about its stories. A `stale`,
     `unavailable`, `incompatible` or `other-epic` entry updates `lastOutcome` and `lastAttemptAt` only, whatever
     stages its body shows.
   - A sibling snapshot that is itself older than `snapshotMaxAgeSeconds` is refused by the freshness gate
     (Decision 3). That is the second way a read is stale. The refusal applies none of its stories or stages and
     journals nothing about them, but it does set each sibling it names to `lastOutcome: stale` and updates
     `lastAttemptAt`. So both ways a read can be stale reach the record the same way.
   - A sibling is fresh when its `lastOutcome` is `read`. Views and `waits` decide "not fresh" from `lastOutcome`
     alone, and never compare times with a clock.
   - Every sibling group on every view shows when its last `read` was taken, whatever its `lastOutcome`. If `serve`
     stops refreshing, nothing marks the sibling stale, but the reader can still see how old the last read is.
   - A sibling story reads landed when its stage is `merged`, or `settled` for a planned entry, the same test as
     `_landed_subjects`.
   - The first `read` showing a story landed journals `sibling.landed` once, with `subject`, `sibling` and
     `firstSeenAt` equal to that read's `readAt`. That is an observation time: the hosted body carries no merge
     time, so the record never presents one as when the merge happened.
7. **Sibling reading is a `doctor` check.** `doctor` lists each sibling with its last outcome, and names a binding
   that has siblings in its record but no `siblingSnapshot` command.
8. **The combined view is a chapter, not a new page.** The live line map, the Markdown view and the built-in page
   gain an "Across repositories" chapter:
   - one group per sibling, with its name, a link to its live view, its stories with their stage words, and the
     time of its last read, whatever its `lastOutcome` (Decision 6);
   - a sibling that isn't `read` and fresh is drawn unavailable, with the reason and the time of its last good read;
   - the chapter is collapsed by default, as the "Elsewhere" section is.

   The headline changes only once every one of this delivery's own stories has landed. Before then it is today's
   headline, whatever the siblings say. Once they have landed, each same-epic sibling (any whose `lastOutcome` isn't
   `other-epic`) is counted:
   - **Fresh and all landed:** contributes nothing.
   - **Open stories in its last kept read, whatever its `lastOutcome`:** "<name> has N open", with "last read <at>"
     added when it isn't fresh.
   - **Never read:** "<name> couldn't be read (never read)".
   - **Not fresh, with no open stories in its last read:** "<name> couldn't be read (last read <at>)". Its last read
     can't show stories added since.

   If any sibling contributes, every view says "This repository's part is done." followed by each sibling's
   sentence, in place of a done headline. The done headline shows only when every same-epic sibling is fresh with
   everything landed. The delivery still closes by itself, because each repository closes its own part. A record
   with no siblings draws exactly as before.
9. **Local views only. The hosted page is unchanged.** This is the operator's call of 2026-11-18. The hosted
   projection drops `siblings` and the sibling journal kinds' facts, and `hosted-delivery-snapshot` stays at `/2`.
   Carrying siblings on the hosted page, and deciding whether a sibling's read link may appear on another delivery's
   public page, is V26-3605.
10. **"Elsewhere" and siblings meet.** V26-3363's outside children that a sibling holds, matched by `subject`, are
    drawn in that sibling's group with its stage and link. Outside children that no sibling holds stay in
    "Elsewhere" with their tracker status only.

    V26-3363 changes nothing without `--scope`, and Linear's snapshot gets no `--scope`, so siblings need their own
    rule. The tracker sweep treats a child as outside when its subject appears in any sibling's last kept read and no
    story of this delivery holds it, with or without `--scope`. It never adds such a child as planned work or a
    follow-up and never reports on it. It also clears a tracker-added copy already in the record the way V26-3363's
    part C does, journaled once with the reason "held by sibling <name>". The delivery's own stories are never
    eligible.
11. **An `after` key may name a sibling's story.** `waits` gains the cause `sibling`, placed after `order` in
    `WAIT_CAUSES`. It is raised for an `after` key that no story of this delivery holds and that a sibling's stories
    hold unlanded. Its sentence in `wait-words.json` is "Waits for {on:ticket} to land in {sibling}. {where}", and
    `{where}` says "last read {at}" when the sibling isn't fresh (Decision 6). `frees` is `landed`, and
    `needsSomeone` is false.
    - `next` treats the key as an open blocker until the sibling's story reads landed.
    - `delivery_state.py wait` wakes on `sibling.landed`.
    - `lead-plan change --after` accepts a key that a sibling's stories hold.
    - `lead-setup` already notes an `after` key the plan has no story for. Its note now also accepts a key a known
      sibling's stories hold, and it still doesn't refuse, since siblings may not have been read yet.
12. **An unresolved `after` key changes behaviour only in a record with siblings.** If a record has at least one
    sibling, an `after` key held by no story and no sibling raises a `sibling` wait.
    - **Every same-epic sibling's `lastOutcome` is `read`:** the sentence says no sibling lists the key, and the
      wait has `needsSomeone: true`, because the lead has to add the sibling or fix the key.
    - **Any same-epic sibling not fresh or never read:** the sentence says that sibling hasn't been read yet, and
      `needsSomeone` is false, so the lead isn't told to fix a key that may be right.

    Either way the story is held. In a record without siblings, such a key is ignored, as today. That pins today's
    behaviour for every existing record.
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
- **Operator:** after the full local run on macOS with Python 3.11, tag a `-rc.N` candidate, run the two-repository
  proof on it, then tag the final on the same commit.
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

V26-3603. Every reader test uses a loopback receiver that records each request's path and headers:

1. `lead-sibling add` on a record with `meta.trackerParent` journals `sibling.added`, and `remove` journals
   `sibling.removed`. On a record without `trackerParent`, `add` is refused and nothing is written. A non-HTTPS
   link other than loopback is refused.
2. Against a receiver serving a valid `/2` body with a story at `building`, `hosted.py siblings` prints
   `outcome: read` with that story. `reconcile` applies it, and the record's sibling holds the story at `building`,
   with `readAt` and `lastOutcome: read`.
3. With the binding's publishing-token variable set in the environment, no request the reader makes to `/current`
   or `/snapshots/{digest}` carries an `Authorization` or `Cookie` header.
4. A receiver that answers `/current` with a 302 to a second receiver reads as `unavailable`, and the second
   receiver records no request.
5. After scenario 2's read, three rejected bodies each list the same story at `merged`: one whose sha256 doesn't
   match its digest, one with a `rendererVersion` of `/3`, and one whose fingerprint doesn't match. Each reads as
   `incompatible`. In each case, after `reconcile` the record's stage for the story is still `building`, no
   `sibling.landed` is journaled, and `lastOutcome` is `incompatible`.
6. A body with a matching digest and fingerprint that the `/2` schema refuses reads as `incompatible`, with the
   same assertions as scenario 5.
7. A receiver that times out or returns 500 reads as `unavailable`. The record keeps scenario 2's story at
   `building`, and `lastOutcome` says `unavailable`.
8. A sibling whose `trackerParent` differs, and whose body lists a story at `merged`, reads as `other-epic`. Its
   story is neither stored nor journaled as landed, and `sibling.mismatched` is journaled once across two
   reconciles. When that sibling's parent changes again, it is journaled once more.
9. Staleness, two ways, each from scenario 2's record and each with the story at `merged`:
   - (a) `current` says `stale: true` with a recent `sourceConfirmedAt`, and the body is not terminal. It reads as
     `stale`, `reconcile` leaves the stored stage at `building`, and no `sibling.landed` is journaled.
   - (b) `current` says `stale: false`, but the sibling snapshot's `readAt` is older than `snapshotMaxAgeSeconds`.
     `reconcile` refuses it, the stored stage stays `building`, no `sibling.landed` is journaled, and the
     sibling's `lastOutcome` is `stale`.

   A fresh read then journals `sibling.landed` once, with `firstSeenAt` equal to that read's `readAt`. A later read
   journals nothing more.
10. A sibling whose last cut is terminal (`delivery.closed` last among the viewer's terminal kinds), with a story at
    `merged`, and whose `current` says `stale: true`, reads as `read` with `terminal: true`. It journals
    `sibling.landed` and is drawn as read, not stale. A body that reopens after `delivery.closed`, with a later
    `lane.assigned`, read stale is `stale`. So is a terminal body whose `current` names a newer pointer than the
    body read.
11. With a binding declaring `siblingSnapshot`, `serve`'s refresh runs it with `{stateDirectory}` filled in and
    applies the result. `doctor` names a record with siblings whose binding has no `siblingSnapshot`.
12. The hosted projection of a record with siblings has no `siblings` member and validates as `/2`.

V26-3072. Each case is checked on the line map, the Markdown view and the built-in page:

13. A record with two siblings, one `read` and one `unavailable`, draws an "Across repositories" chapter with both
    groups. The read group shows the time of its last read. The unavailable group shows its reason and the time of
    its last good read. A sibling never read shows "never read".
14. Headline cases:
    - (a) Own stories all merged, and a fresh sibling with two open stories: "This repository's part is done.
      <name> has 2 open." The delivery still closes.
    - (b) Own stories all merged, and every sibling fresh with every story landed: the usual done headline, and no
      "part is done".
    - (c) Own stories all merged, and the sibling's `lastOutcome` is `unavailable` with two open stories in its last
      kept read: "<name> has 2 open, last read <at>", never the done headline.
    - (d) Own stories all merged, and a sibling never read: "<name> couldn't be read (never read)", never the done
      headline.
    - (e) Own stories not all landed: today's headline, with neither the part-done nor the done headline.
    - (f) Own stories all merged, and the sibling's `lastOutcome` is `unavailable`, then `stale`, with every story
      landed in its last kept read: "<name> couldn't be read (last read <at>)", never the done headline.
    - (g) Own stories all merged, with one fresh same-epic sibling whose stories have all landed and one
      `other-epic` sibling whose earlier read holds open stories: the usual done headline, and the `other-epic`
      sibling isn't named.
15. A V26-3363 outside child that a sibling holds is drawn in that sibling's group, and one no sibling holds stays
    in "Elsewhere".
16. With no `--scope` set, a tracker snapshot showing a `started` child whose subject a sibling's last read lists
    adds nothing to the record. A tracker-added copy of it already in the record as lane-less building work is
    cleared, journaled once with "held by sibling <name>", and a second sweep journals nothing more. With the
    sibling's `lastOutcome` then `unavailable`, its last kept read still listing the subject, a tracker snapshot
    showing the child `started` adds nothing and doesn't re-add the cleared copy. A story of this delivery with the
    same subject is never cleared.
17. A record with no siblings renders byte for byte as the current goldens.

V26-3604:

18. Characterization: in a record without siblings, an `after` key naming a ticket outside the record raises no
    wait, and `next` offers the story. This is pinned before any change.
19. A planned story `after` a sibling's `building` story shows "Waits for KEY to land in <sibling>. Building." and
    `next` doesn't offer it.
20. When a fresh read shows that story `merged`, the wait is gone, `next` offers the story, and `wait` returns on
    `sibling.landed`. No lead command ran.
21. Scenario 9's two stale cases, each showing the story `merged`: the wait stays and says when the sibling was
    last read, and `next` still doesn't offer the story. A fresh read then clears it.
22. With the sibling `read`, an `after` key held by no story and no sibling raises a wait with
    `needsSomeone: true`, and it shows in "Needs you". With the sibling `unavailable` or never read, the same key
    raises a wait that says the sibling hasn't been read yet, with `needsSomeone: false`. With one same-epic sibling
    `read` and one `other-epic` sibling, the key raises the `needsSomeone: true` wait.
23. With the tracker's `blockedBy` still open and the sibling's story `merged` in a fresh read, `next` doesn't offer
    the story. Once both say done, it does.
24. `lead-plan change --after` accepts a sibling's story key and still refuses a key held by nothing. `lead-setup`'s
    note names a plan `after` key that is neither a story nor a known sibling story, and doesn't name one a sibling
    holds.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3363 | `skills/delivery-views/scripts/snapshot.py`, `tests/test_delivery_views_snapshots.py`, `tests/fixtures/delivery-views-snapshots/jira-scope/` |
| 1 | 2 | V26-3603 | `skills/delivery-views/scripts/hosted.py`, `tests/test_siblings.py`, `tests/fixtures/siblings/` |
| 2 | 1 | V26-3072 | `tests/delivery_views_page/line-map-siblings.test.mjs`, `tests/test_siblings_sweep.py` |
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
`siblings` from the hosted projection by Decision 9. Scenarios 1 to 12.

### V26-3072. Show one record for an epic delivered across repositories

Lane 1, tier 2, after V26-3363 and V26-3603. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/delivery-views/scripts/render.py`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/delivery-views/SKILL.md`, `catalog.json`, `profiles/`,
`skills/orchestrate-work/scripts/delivery_state.py`, `tests/delivery_views_page/line-map-siblings.test.mjs`,
`tests/test_siblings_sweep.py`, `tests/test_delivery_views_page.py`, `tests/test_delivery_views_render.py`, `tests/test_orchestration_render.py` and `tests/fixtures/delivery-views/`.

Draw the "Across repositories" chapter and the part-done headline by Decision 8, and merge "Elsewhere" with siblings
by Decision 10, on the line map, the Markdown view and the built-in page. Make the tracker sweep treat a
sibling-held child as outside by Decision 10. Scenarios 13 to 17.

### V26-3604. Let a story wait on a sibling delivery's story landing

Lane 2, tier 2, after V26-3603. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/wait-words.json`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/delivery-views/assets/line-map.js`, `catalog.json`,
`profiles/`, `tests/test_waits_sibling.py`, `tests/test_waits.py`, `tests/test_lead_wait.py`,
`tests/test_run_delivery.py` and `tests/test_delivery_views_page.py`.

Pin today's ignored-key behaviour first (scenario 18). Then add the `sibling` cause, `next`'s blocker, `wait`'s
wake, and the `lead-plan` and `lead-setup` changes, by Decisions 11 to 13. Scenarios 18 to 24.

### V26-3622. Word a sibling wait's time from when its outcome began, not its last changing read

Lane 2, tier 2, after V26-3604. Added mid-delivery: V26-3604 deferred P2 outcome-correctness-r1-2: the wait sentence and the schema still say last read from readAt, which can understate recency by hours. The operator chose a lane fix with review.

## Calls made

- Federation over one delivery spanning repositories: agreed with the operator on 2026-11-18, before planning.
- Local views only, with the hosted page as V26-3605: the operator, 2026-11-18.
- V26-3363 folded in as tier 1: the operator, 2026-11-18.
- Plan review runs unbounded, as `plan-work` records, and ends only on alignment or by the operator's decision.
