---
reviewSlug: ownership-follows-work
---

# Keep lane ownership in step with the work

In V26-3553, spans and grants made more work for the lead than anything else. About 40 stale spans were regranted by
script after landings. Three carves left lines unowned, and one of those blocked a lane's review. A lane asked for a
span two lines off and it was granted unchecked. Lanes learned of a missing grant one refusal at a time, and that they
had changed a file they didn't hold only when `lead-accept` refused the work after review. The ownership lock that
makes a transfer wait for a lead acceptance has no regression fixture.

Epic V26-3619. The incidents are in V26-3553's gap log, `docs/plans/2026-11-17-what-everything-waits-on.gaps.md`,
under "Ownership and region claims".

Checked against `main` at `a904245` on 2026-11-18:

- A region claim in the record's `/laneRegions` is `{lane, path, baseHead, start, end}`: a half-open span of lines in
  the blob at `baseHead`. It names no story. The state tool's validation refuses a claim on a path that has a
  whole-file claim, and an overlap with an earlier claim at the same `baseHead`.
- `region_ownership.mapped` maps a claim from its `baseHead` to a later base. It refuses when the claim's base is
  outside the target's history, when any base edit intersects the span or touches its edge, and when repeated lines
  make the mapping ambiguous. `check_change` allows a hunk that removes lines only when they all lie in one span, and
  an insertion only strictly inside one span. It raises at the first hunk that fails, so every caller reports one
  problem per path.
- `change_regions` (`lead-own --region` and `--revoke-region`) maps every claim on the path to the current base before
  it grants, so one claim that no longer maps refuses every grant on its file. It writes through `patch_from`, which
  recomputes on `version_stale`. A carve is a revoke and then grants, two commands, so a refused grant leaves the
  revoked lines unowned. Each grant prints only its coordinates.
- `candidate_ownership` (review preparation, hand-off, acceptance) and the merge queue's `check_regions` call
  `region_ownership.problems` on their own diffs. The queue checks the merged tree against the current base, and its
  copy of that logic lives in `merge_queue.py`.
- On `candidate.merged`, nothing touches `/laneRegions`. A landed story's spans stay, anchored before its own edits,
  and no longer map.
- `ownership_lock` serializes `lead-own` with `lead-accept` through `<state>/lane-ownership.lock`.
  `RegionOwnershipTests` in `tests/test_run_delivery_supervisor.py` covers grants, overlaps, trespass and revocation.
  Nothing tests the lock.
- The supervisor's pass writes `needs-you.json` and `peer-waits.json` beside the record, and the line map reads them.
  No pass reads a lane's pushed branch, and the page has no view of who holds which files.

## Finish line

- Both plan lenses align.
- Each story's acceptance criteria hold, proven by the tests named under Scenarios.
- All four stories land on `integrate/ownership-follows-work` through its merge queue.
- The integration base has been reviewed for `main`, as **Reviewing an integration base for the default branch** in
  the orchestrate-work guide describes, and merged into `main`. The full local suite has passed on macOS with Python
  3.11 at `main`'s head after that merge, run with `--workers 4 --budget tests/suite-budget.json`, inside the budget.
- This delivery's own grants, hand-offs and queue run on the installed generation, so they can't show the new
  behaviour. The fixture tests are the proof.
- Tagging a release is not this delivery's. Every change is additive, a minor version, released at the next batch
  boundary.

## Decisions

1. **A claim may name its story.** A region claim gains an optional `story`, a key of this delivery assigned to the
   claim's lane. `lead-own --region ... --story KEY` sets it. A claim without one is the lane's. The state tool's
   schema and validation admit the field and refuse a key the record doesn't hold. Every existing record stays valid.
2. **When a story lands, its claims go and its lane's other claims are carried.** V26-3591 c1. For each
   `candidate.merged` of a story of lane L, at merge commit M, the supervisor's pass makes one versioned patch under
   the ownership lock:
   - Claims whose `story` is the landed story are removed, and `ownership.released` is journalled for each.
   - Each other claim on a path the merge changed is mapped to M. A claim that maps stays as it is. It is not
     re-anchored, so it still maps for a lane whose branch began before M.
   - A claim of lane L that does not map is carried when every hunk from its `baseHead` to M that touches it lies wholly
     inside it, with no hunk crossing either edge. That is the boundary check the lead ran by hand. The carried claim
     covers the same lines with the hunks inside absorbed, is anchored at M, keeps its `story`, and replaces the old
     one. `ownership.carried` is journalled with the old and new coordinates. When another story of lane L is in
     flight on a branch that began before M, the carry names it, since that story's claim now maps only after it
     merges the base.
   - Any other claim that does not map is left in place, and "Needs you" names it as needing a regrant, with its
     reason. The item does not hold the close, and clears when the claim is regranted or revoked.
   - A claim whose `baseHead` is not a strict ancestor of M is left untouched when settling M: it was granted at or
     after the merge, so the merge is already in its history.
   Each merge is settled once, in journal order: the record keeps the id of the last `candidate.merged` it settled, in
   the same patch as the claims, so a restarted supervisor neither skips nor repeats one. `lead-init` writes the
   settled id as 0 for a new record, and so does a pass that finds no settled id and no `candidate.merged` in the
   journal. Only a record that already holds a `candidate.merged` and no settled id, one started by an earlier
   release, is seeded by the first pass with the latest `candidate.merged` id, and no earlier merge is settled.
   The pass journals its `ownership.released` and `ownership.carried` entries after that patch, so a supervisor
   stopped between the two loses the entries, never the change. The settle takes the ownership lock, as `lead-own`
   and `lead-accept` do.
3. **A claim that no longer maps doesn't block a grant on its file.** `lead-own --region` checks overlap against the
   claims that map at the current base. It grants past one that doesn't, and prints it as needing a regrant, rather
   than refusing. The state tool's same-base overlap check is unchanged.
4. **A carve is one command, applied whole or not at all.** V26-3591 c2.
   `lead-own --carve PATH --base B --give LANE:START-END[@KEY] [--give ...]` computes the whole new set of claims on
   PATH in one `patch_from` compute: every existing claim on PATH is mapped to B, the lines each give covers are cut
   out of the claims they overlap, the remnants stay with their lanes and stories, and the gives are added. Every
   resulting claim is anchored at B. B must be a commit every affected lane's candidate descends from, as for any
   grant today, and defaults to the current base head. The carve is refused, changing nothing, when an existing claim
   on PATH doesn't map to B, when the result has an overlap or a claim that doesn't map to the current base, or when a
   give names a lane the record doesn't hold. A `version_stale` retry recomputes the whole carve from the fresh record.
   One `ownership.carved` entry is journalled with the claims removed and added. Revoke-then-grant still works.
5. **A lane's touching claims become one.** V26-3591 c3. When a grant or a carve leaves two claims of one lane on one
   path, with the same `story` (or neither has one), whose spans touch or overlap once mapped to the grant's base, they
   are replaced by one claim anchored at that base. Claims of different lanes or stories are never merged.
6. **Ownership problems are reported all at once.** `region_ownership` gains a report that walks every hunk of a diff
   and says, for each, which claim holds it or that none does, instead of raising at the first. `check_change` and
   `problems` keep their verdicts and read the report. Review preparation, hand-off and acceptance list every path and
   every hunk outside the lane's claims in one refusal. The lane command and the lead command below print the same
   report.
7. **A lane checks its own claims.** V26-3591 c4. A new lane command, `claims-check <key>`, run in the lane checkout,
   reports the committed diff from the merge base with the current base to `HEAD`: each changed path that a
   whole-file claim of the lane holds, each hunk inside one of its spans, and every hunk, creation, deletion, rename,
   mode or binary change it still needs a claim for. For each needed span, it prints the request for the lead in the
   form the lead runs. It exits 0 when nothing is needed and 1 otherwise, writes nothing, and journals nothing.
8. **The lead previews the queue's ownership check.** V26-3591 c5. A new lead command, `lead-claims [KEY...]`, takes
   each named story's handed head, or its pushed branch when it hasn't handed off yet. It builds the tree the queue
   would sync with `git merge-tree --write-tree` against the current base head, and runs the queue's own region and
   whole-file check on it. It prints whether the queue would accept the candidate's ownership, and each lane's
   touched lines against its claims (Decision 6's report). With no keys, it covers every story with a hand-off or a
   pushed branch. The queue's check moves from `merge_queue.check_regions` into `region_ownership`, so the queue and
   the preview run one function. A head that doesn't merge cleanly is reported as such, without an ownership verdict.
9. **A grant shows its lines.** V26-3570 c1. Every region grant, carve and carry prints, for each span it writes, the
   first and last line as text at its base, cut to 100 characters each.
10. **A grant can name its lines by anchor.** V26-3570 c2. `lead-own --region PATH --base B --around TEXT` stands in
    for `--start` and `--end`, and so does `--give LANE:around=TEXT[@KEY]` in a carve. TEXT matches a line at B
    containing it as an exact substring, and a full quoted line matches only lines that contain all of it. No matching
    line is refused, and so is more than one, listing each candidate's line number and text, at most 10. The span is
    the enclosing block of the one match:
    - In a Python file, the outermost statement starting on that line, with its decorators, from the standard
      library's `ast`. On a line inside a statement, the smallest statement containing it.
    - In a Markdown file, a heading's section up to the next heading of the same or higher level. Any other line's
      paragraph, up to a blank line.
    - In any other text file, the line, and the following lines indented deeper than it, and a closing line at its
      own indentation that starts with `}`, `]` or `)`.
11. **A span may not cut a Python statement in two.** V26-3570 c3. In a Python file, each edge of a granted or carved
    span must fall at the start of a logical line, from the standard library's `tokenize`, or at the end of the file.
    Otherwise the grant is refused, naming the statement and its lines. Other languages have no parser in the
    standard library, so no statement check applies to them. Neither does the check apply at acceptance or in the
    queue (V26-3570's scope).
12. **An insertion at a span's edge widens the grant.** V26-3570 c4. `--insert before` or `--insert after` on a grant
    or a give widens the span by one line past that edge, so an insertion there falls strictly inside it, and prints
    the widening. The line taken must exist and be unclaimed. Otherwise the grant is refused, saying which. An insertion
    at the end of the file still needs a whole-file claim, since how regions are checked stays as it is.
13. **The lane brief asks by anchor.** V26-3570 c5. The brief tells lanes to run `claims-check` before review, and to
    ask the lead for spans by anchor or quoted line, with `--insert` for an insertion at an edge, never by bare line
    numbers. Once V26-3570 lands, `claims-check` prints its requests in that form.
14. **The ownership lock is proven without a production test hook.** V26-3476. The fixture pauses `lead-accept`
    inside its lock at an adapter it calls there: a fixture adapter script records its pid and blocks until the test
    signals it. While it is paused, the test starts `lead-own --move`, and proves the move has neither written the
    record nor exited. Then it releases the adapter, and proves the move completes after the acceptance's journal
    entry. The lane proves it red with the lock removed (`ownership_lock` made a no-op) and records that run in its
    evidence. Production code gains no pause point. If `lead-accept` calls no adapter inside the lock in the fixture's
    configuration, the lane names that in its hand-off instead of adding a hook, and the lead decides.
15. **A pass reads each lane's pushed branch.** V26-3564 scope 1. Each supervisor pass, after its fetch, reads every
    assigned lane's story branch on the remote, as a diff from its merge base with the delivery base. It writes
    `lane-changes.json` beside the record, with spec `delivery-lane-changes/1`: per lane, its story, branch and head,
    and per changed file, whether the lane holds it and each change's first line, size and enclosing name on the new
    side. The enclosing name is the nearest `def` or `class` in Python, `function` or `const NAME =` in JavaScript,
    and heading in Markdown. A branch read at the same base and head as the last pass is not read again. A story with no
    pushed branch has no entry. A branch that exists but can't be read has an entry saying why, with no changes.
16. **"Needs you" names a change outside a lane's ground.** V26-3564 scope 2. For each changed file its lane doesn't
    hold, by the same ownership acceptance uses, the list gains a shown item that doesn't hold the close: "Lane 1
    changed `foo.py`, which it doesn't hold." A declared shared output, such as `provenance.lock.json`, raises
    nothing. The item clears when a pass no longer sees the change, or the lead grants the file. On a file the lane
    holds only by span, each change outside its spans raises the same item, naming the lines: "Lane 1 changed lines
    40-44 of `foo.py`, outside its span." A change inside a span raises nothing. A branch that can't be read raises a
    shown item saying so, in place of the lane's items.
17. **The page shows who holds which files when lanes share files by lines.** V26-3564 scope 3. When the record has
    region claims, the "Who's doing what" chapter offers a "Who holds which files" view, closed by default. It shows the
    delivery's claimed files grouped by area, the folder under the repository root they sit in, each solid where its
    lane has changed it and hollow where it hasn't, and each shared file split by its lanes' line stretches. Clicking a
    shared file opens a reading: a ruler of the file with each lane's stretches and changes, and per lane, the
    functions changed with their sizes. A lane whose branch can't be read is shown so, never with old changes as
    current. With no region claims, the page shows no such view.
18. **The catalog and profile sentences are the lead's.** Each story changes the one-line `degradedBehavior` of
    `catalog.json` and the one-line `description` of each `profiles/*.json`, so the lanes would conflict there. The
    lead writes each story's sentence in one commit on the integration base, pushed with `lead-push` right after the
    story lands. `provenance.lock.json` stays regenerable: each lane runs the writer.
19. **The lead enters the new test modules before lanes start.** `tests/test_own_deliveries.py` pins `CATALOGUE_MODULES`
    against the test modules on disk both ways, and `.agents/orchestration.json`'s `selection.catalogue` must match it.
    Before the first assignment, the lead commits the four new modules on the integration base as stubs, each with only
    its module docstring, and enters them in both lists in the same commit. Each lane then owns its modules whole, and
    no lane touches either list.

## Out of scope

- A lane reading its grants from the record (V26-3571).
- Changing how regions are checked at review preparation, acceptance or in the queue, beyond reporting every problem
  at once (Decision 6). An insertion at the end of a file still needs a whole-file claim.
- A statement check for languages other than Python (Decision 11).
- Refusing anything on a lane's pushed branch. V26-3564 shows; acceptance still decides.
- Granting against each lane's own merge base. Decision 4's `--base` is the lead's common commit, as today.
- `lead-assign`'s co-change warning missing files a story needed (V26-3553's gap log). It is not closed here.
- A release tag.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants lanes `push` and `draftPullRequests`, and the lead `readyPullRequests` (granted 2026-11-18).
- The operator approves merging `integrate/ownership-follows-work` into `main` after its review.

## Execution posture and sensors

Test-first for every story. Sensors:

- `tests/test_region_claims.py`, new, for Decisions 1 to 8: release and carry on landing, the carve, merging touching
  claims, the all-at-once report, `claims-check` and `lead-claims`.
- `tests/test_region_grants.py`, new, for Decisions 9 to 13: printed lines, anchors, the statement check and widening.
- `tests/test_merge_queue.py`'s `RegionDiffTests`, unchanged, and `tests/test_run_delivery_supervisor.py`'s
  `RegionOwnershipTests`, which runs the real queue, for the queue's check after it moves.
- `tests/test_journal_shape.py`'s inventory for the new journal calls, and `tests/test_orchestration_state.py`'s guide
  tracker table check for the new kinds' rows in the orchestration guide.
- `tests/test_own_deliveries.py`'s catalogue guard for the new modules (Decision 19).
- `tests/test_ownership_lock_race.py`, new, for Decision 14.
- `tests/test_lane_changes.py`, new, for Decisions 15 and 16.
- `tests/delivery_views_page/line-map-files.test.mjs`, new, for Decision 17.
- `tests/test_journal_kinds.py` and `tests/fixtures/journal-kinds/vectors.json` for `ownership.released`,
  `ownership.carried` and `ownership.carved`.
- `RegionOwnershipTests`, unchanged, also as the regression for grants, overlaps, trespass and revocation.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check` for provenance.

Most of Decisions 2 to 13 is `region_ownership` logic, tested on small repositories built with `tests/quiet_git.py`,
with no installed release. The command-level cases share one class fixture per module, built once, as
`RegionOwnershipTests` builds its own. The race fixture waits on real processes within a bound, so its class is
`run_quietly`. The suite stays inside `tests/suite-budget.json`.

## Scenarios

V26-3591, in `tests/test_region_claims.py`:

- A story with a claim of its own and its lane's claim on the same file lands. The story's claim is gone, with
  `ownership.released`. The lane's claim, whose lines the story changed inside it, is carried to the merge commit
  over the same lines, with `ownership.carried`. A later grant on that file to another lane succeeds.
- The carried claim belongs to another story of the lane, S2, still in flight. The record then holds exactly
  `{lane, path, baseHead: M, start, end, story: S2}`, and when S2 lands that claim is released.
- A lane claim that a landed hunk crosses at an edge is not carried. "Needs you" names it as needing a regrant. The
  item does not hold the close, and clears once the claim is regranted, and in a second case once it is revoked.
- Lane K's claim starts where lane L's span ends, and L's landed hunk replaces L's last line. K's claim is not
  carried, keeps its coordinates and `baseHead`, and is named as needing a regrant.
- Another lane's claim on the same path the merge changed, which the landing doesn't touch, keeps its `baseHead` and
  coordinates.
- A carry when another story of the same lane is in flight on an older branch names that story.
- Two merges land before one pass, and the lead grants a claim at the second merge on a file the first changed. The
  pass settles both merges in order, and leaves that claim untouched, with no regrant item.
- After a settle, the lead grants a claim on the landed path anchored after the merge. A restarted supervisor's pass
  leaves it unchanged with no regrant item, and the record's settled id is that merge's `candidate.merged` id.
- Two stories land, and the supervisor stops after settling the first. After the restart, the second is settled,
  with its own `ownership.released`.
- A record started by an earlier release, with no settled id and two past merges, is seeded on the first pass. Its
  fixture holds a story-less claim of the first merge's lane, anchored before that merge, whose lines the merge
  changed inside it, and another lane's claim at an edge that merge crossed, so a settle would carry the one and flag
  the other. After the first pass both claims are byte-identical to before, no `ownership.released`,
  `ownership.carried` or `ownership.carved` is journalled, "Needs you" has no regrant item, and the settled id is the
  latest merge's id.
- A record started by `lead-init` holds a settled id of 0. After one pass with no merge, its first story lands, and the
  next pass settles that merge, with `ownership.released` for the story's claim.
- A record with no settled id and no `candidate.merged`, a `lead-init` record with the field removed, standing in for
  one from an earlier release, gets a settled id of 0 from its first pass. A story with its own claim then lands, and
  the next pass settles that merge, with `ownership.released` for the story's claim and the settled id equal to that
  merge's id.
- A grant on a file where another lane's claim no longer maps succeeds and prints that claim as needing a regrant. On
  the same fixture, with the record unchanged each time, a grant whose own span doesn't map to the current base is
  refused, and so is a grant that overlaps a third claim that does map, naming it.
- `lead-own --region --story` with a key the record doesn't hold is refused, and the state tool refuses a record
  carrying one. A record whose claims have no `story` still validates.
- A carve gives lines out of two lanes' claims, one of them a claim with a story, and one give uses `@KEY`. The record
  then holds exactly the expected claims, each with its lane, story, start, end and `baseHead` B, and one
  `ownership.carved`.
- A carve with one give that would overlap after mapping changes nothing. So does one where an existing claim doesn't
  map to `--base`, one with a give to a lane the record doesn't hold, and one with a `--base` older than the current
  base whose result doesn't map at the current base.
- A carve whose first write meets `version_stale`, because a concurrent write changed a claim on the carved path. The
  final record holds that write with the carve computed over it, and `ownership.carved` lists the second compute's
  removed and added claims.
- Granting a span that touches the same lane's span leaves one claim, and an edit replacing lines across the old
  boundary is then accepted. Touching spans of different lanes, or of different stories, stay two. Spans of one lane
  and one story with one unclaimed line between them stay two, and that line can be granted to another lane.
- A diff that changes lines outside the lane's span and creates a file it doesn't hold is refused at review
  preparation, at hand-off and at acceptance, each naming both.
- `claims-check` on a clean branch exits 0, writes nothing and journals nothing. Its branch then gains a hunk outside
  its span, a new file, a deletion, a mode change and a whole-file change it doesn't hold, after the base has moved
  with another lane's landed change. It lists all five and not the base's change, and exits 1. The lead runs every
  request it printed, then `claims-check` exits 0 and `lead-claims` says the queue would accept the candidate.
- `lead-claims` says the queue would refuse a candidate whose merged tree trespasses after the base moved, and the
  same candidate is then stopped by a real queue run for the same reason. It says the queue would accept one that
  doesn't trespass. A head that conflicts is reported as conflicting. With a handed head and a newer pushed branch, it
  checks the handed head. With no keys, it covers every story with a hand-off or a pushed branch.

V26-3570, in `tests/test_region_grants.py`:

- A grant, a carve and a carry each print every span's first and last line text, a line over 100 characters cut to
  100.
- Each anchor row asserts the exact `[start, end)` on a fixture with neighbours on both sides:
  - `--around "def waited_on"` grants the decorated function, which is followed by another function, with its
    decorators and without the next one.
  - On a line inside a function body, it grants the smallest statement around it.
  - A Markdown heading grants its section, which holds a deeper subheading and is followed by a heading of the same
    level, up to that heading. A Markdown paragraph's line grants the paragraph.
  - A JSON key's line grants its block, ending with its `}` line and not the sibling key after it.
- A carve with `--give LANE:around=TEXT@KEY` leaves the anchor's block claimed by that lane, with the story.
- An anchor with no match is refused. One with three matches is refused, listing all three lines.
- A Python span starting in the middle of a multi-line call is refused, naming the call and its lines, and so is one
  ending in the middle of one. A Python span ending at the file's end is granted. The same mid-call span in a text
  file is granted. A carve whose give cuts a Python statement is refused, with the record unchanged.
- `--insert after` widens a span by one line and prints it, and an insertion committed at the original edge is then
  accepted at review preparation. `--insert before` widens by one line before the span. A widening into another
  lane's claim, or past the end of the file, is refused.
- After V26-3570, `claims-check`'s requests use `--around` or a quoted line, and `--insert` for an insertion at an
  edge, never bare line numbers.
- The rendered lane brief tells lanes to ask by anchor or quoted line, and to run `claims-check` before review.

V26-3476, in `tests/test_ownership_lock_race.py`:

- With `lead-accept` paused inside its lock, a `lead-own --move` neither writes the record nor exits. Released, the
  acceptance journals first and the move completes after it.
- With the lock made a no-op, the same fixture fails, recorded in the lane's evidence.

V26-3564, in `tests/test_lane_changes.py` and `tests/delivery_views_page/line-map-files.test.mjs`:

- A pushed change to a file the lane doesn't hold appears in "Needs you", naming the lane and file. It clears when the
  change is reverted on the branch, and when the lead grants the file.
- After the lane's branch began, another lane's change to a file this lane doesn't hold lands on the base. This lane
  gets no item, and its `lane-changes.json` entry has no such file.
- A lane holding a span on a shared file pushes two hunks there. The one inside the span raises nothing, and the one
  outside it raises an item naming its lines.
- A change to a declared shared output raises nothing, in a fixture whose shared output is not `provenance.lock.json`.
- A story with no pushed branch has no entry in `lane-changes.json`.
- Enclosing names are read for a Python function, a JavaScript `function` and `const NAME =`, and a Markdown heading.
- A branch that can't be read leaves an item saying so, and the page shows the lane as unreadable, not with the last
  pass's changes.
- `lane-changes.json` names each change's first line, size and enclosing function. On the next pass with the branch
  unchanged, the entry records the same read as before, by its recorded read time, rather than a new one.
- With region claims, the page's files view shows the shelf, solid and hollow files, the shared splits and a file's
  reading, matching the record and the fixture's branches. With none, the page has no files view.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3591, V26-3570 | `skills/orchestrate-work/scripts/region_ownership.py`, `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/templates/lane-brief.md`, `tests/test_region_claims.py`, `tests/test_region_grants.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/` |
| 1 | 2 | V26-3476, V26-3564 | `skills/delivery-views/scripts/render.py`, `skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css`, `skills/delivery-views/SKILL.md`, `skills/run-delivery/references/config.md`, `tests/test_ownership_lock_race.py`, `tests/test_lane_changes.py`, `tests/delivery_views_page/` |

`run_delivery.py` is shared by lines. The installed generation has no carve, so before assigning V26-3564 the lead
revokes lane 1's whole-file claim and grants spans with `lead-own --region`, against the current base, in one run
while neither lane has a candidate under review. If a grant is refused, the lead regrants lane 1 the whole file and
decides again before assigning. The spans:

- Lane 2 gets `needs_you`, `write_needs` and `lane_needs` and the functions between them, except for one lane 1 span
  inside `needs_you`: the `items += lane_needs(doc["state"])` statement and the statement after it, where lane 1 adds
  Decision 2's regrant items strictly inside its span. Lane 2's insertion point for Decision 16's items is strictly
  inside its own span, before `items += record_needs(doc, items)`.
- The `steps = (...)` statement in the supervisor's `one_pass` is one statement, so it goes to one lane: lane 1, which
  adds the landing step there for V26-3591. Once V26-3591 lands, the lead moves that span to lane 2, which then adds
  the branch read (Decision 15) there. Lane 2 builds and tests the branch read before then, and hands off V26-3564
  only after the move.
- Lane 1 keeps the rest.

Every insertion point the assignment message names falls strictly inside the receiving lane's span. V26-3476 needs no
`run_delivery.py` lines under Decision 14. If it does after all, the lead decides before granting any.

`catalog.json` and `profiles/*` are the lead's (Decision 18), and so are `tests/test_own_deliveries.py` and
`.agents/orchestration.json`'s catalogue (Decision 19). `provenance.lock.json` is regenerable.

V26-3570 changes `change_regions` and the carve after V26-3591 reshapes them, so lane 1 runs them in order. V26-3476
is small and stands alone, so lane 2 starts with it. V26-3564 depends on nothing here: it judges a file held by the
ownership acceptance already uses. If V26-3591's report (Decision 6) has landed by then, it may read that instead.

## Stories

### V26-3591. Make region spans follow the work: release on landing, carve in one step, and let a lane check its own claims

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/region_ownership.py`,
`skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `tests/test_region_claims.py`, `tests/test_journal_kinds.py`,
`tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/`.

Implement Decisions 1 to 8. Add `ownership.released`, `ownership.carried` and `ownership.carved` to the kind registry
with their vectors, their rows in the orchestration guide's tracker table, and the new journal calls in
`tests/test_journal_shape.py`'s inventory. Add `claims-check`, `lead-claims` and the carve to `SKILL.md`'s
command table and its Lane ownership section.

### V26-3570. Make region grants show and resolve their lines, so a wrong span can't pass unnoticed

Lane 1, tier 1, after V26-3591. Test-first. Files: `skills/orchestrate-work/scripts/region_ownership.py`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/templates/lane-brief.md` and `tests/test_region_grants.py`.

Implement Decisions 9 to 13, in `lead-own --region`, the carve's gives and `claims-check`'s requests. Update the lane
brief and the Lane ownership section of `SKILL.md`.

### V26-3476. Prove ownership transfers wait for lead acceptance with a deterministic race fixture

Lane 2, tier 1, not blocked. Test-first. Files: `tests/test_ownership_lock_race.py`.

Implement Decision 14.

### V26-3564. Say when a lane changes a file it doesn't hold, and show shared files when lanes split them

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/assets/line-map.js`, `skills/delivery-views/assets/line-map.css`,
`skills/delivery-views/SKILL.md`, `tests/test_lane_changes.py` and `tests/delivery_views_page/`.

Implement Decisions 15 to 17. Describe `lane-changes.json` in run-delivery's `references/config.md` beside
`needs-you.json`, and the files view in delivery-views' `SKILL.md`.
