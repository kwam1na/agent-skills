---
reviewSlug: shared-generated-files
---

# Lanes stop handing generated files to each other

Six backlog items, two lanes. Lane 1 lets lanes share the generated files a runner can reproduce (V26-3741), makes
catalog and profile wording one sentence per line so stories stop colliding there (V26-3611), and closes three
deferred findings on lane ownership (V26-3703, V26-3700, V26-3701). Lane 2 tells an integration lead when other
deliveries are merging into its target (V26-3616). Each was checked against `main` at `c2fe0981` on 2026-11-24, and
each story's section says what that check found.

Start from `main` at `c2fe0981`, which has the verified `linear-v4.2.0` release installed. The delivery base is the
operator's decision at approval (Approval Handoffs). The generation that delivers this work is not the one that will
contain it.

## Finish line

- Both plan lenses align.
- Each of the six stories meets its tracker criteria and lands on the delivery base through the merge queue, or
  closes with its reason recorded.
- Before the work reaches `main`, the full local suite passes on macOS with Python 3.11 at the final candidate, run
  with `--workers 4 --budget tests/suite-budget.json`, and the suite stays inside the budget.
- CI's `cut-release` job passes for that push to `main`.

## Decisions

The operator approves these with the plan. Each answers a question a ticket leaves open.

1. Two lanes. Lane 1 runs V26-3611, V26-3703, V26-3700, V26-3701 and V26-3741 in that order. Lane 2 runs V26-3616
   once V26-3611 has merged, so its catalog and profile wording goes in as list items.
2. The base keeps the `focused` posture. A lane runs its story's modules and the two gates, the lead selects each
   candidate's checks from the modules its section names, and the full suite runs once, at the final candidate.
3. Lanes may push and open draft change requests, and the lead may mark them ready.
4. No story changes this repository's own binding, `.agents/orchestration.json`, beyond cataloguing a new test
   module. The installed release refuses a binding member it does not know. Declaring `regenerable.shared` here
   waits for a release that holds it.

For V26-3741:

5. A binding declares shared outputs with a new member, `regenerable.shared`: a list of repository paths or patterns,
   each matching an entry of `regenerable.paths`, and only where `regenerable.regenerators` is non-empty. A declared
   listing stays a shared output as it is today. A path under `regenerable.paths` that is neither declared shared nor
   a listing keeps today's ownership rules. A binding without `shared` behaves exactly as it does today.
6. Generator definitions are never shared. A repository file that a regenerator's argv names, the binding file
   itself, and every path outside `regenerable.paths` keep today's ownership rules, whatever `shared` matches.
   `binding_errors` refuses a `shared` pattern that matches the binding file or a file a regenerator's argv names.
7. The binding that decides what is shared is the base's, never the candidate's. The queue already reads it from the
   base head. A lane already reads it from `remote/base`. The lead reads it from the base branch's head for
   ownership and verification, instead of its working tree. A candidate that changes `regenerable` in the binding
   gets no exemption from its own change: its shared outputs are judged by the base's declaration, and the binding
   file is an owned file.
8. A lane's change to a shared output needs no claim. In its place, review-prepare, review-reduce and handoff verify
   it: they check out the candidate's exact commit in a temporary worktree, run the base binding's regenerators
   there, and compare each changed shared output with the candidate's bytes. Verification runs only when the
   candidate changes a shared output, and once per commit: the result is kept in the lane's state folder by commit,
   so the second and third commands at the same head reuse it.
9. A shared output whose rerun differs from the candidate's bytes stops the command with
   `shared_output_irreproducible`, naming each path and the regenerator. A regenerator that exits non-zero, or runs
   past its timeout, stops it with `shared_output_generator_failed`, naming the argv, its exit status and the last
   lines of its error output. Neither leaves a worktree or a file behind.
10. A listing's membership guard is unchanged. Adding or removing an output a listing names still needs the lane to
    own the listing alone, at handoff and at sync, whether or not the listing is also declared shared. A
    digest-only change still passes from any lane.
11. `lead-accept` accepts a hand-off whose only out-of-lane changes are verified shared outputs, and verifies again
    when the candidate's head differs from the one the hand-off verified. `--take` keeps refusing a shared output, as
    it does today for a listing.
12. The queue verifies the candidate's own shared outputs before it merges the base in, with the same two stops, so a
    head pushed after hand-off cannot carry a hand edit. After the merge, the existing regeneration settles the
    outputs as it does today, and `unstable_regeneration` keeps its meaning. A conflict in a shared output is a
    conflict inside the regenerable paths, which `merge_in` already resolves by regeneration.

For V26-3611:

13. The catalog's `degradedBehavior` and a profile's `description` each accept a list of non-empty strings, one
    sentence per item, as well as a string. `scripts/validate-registry.py` refuses an empty list and an empty item.
14. The release builder joins each list with single spaces when it writes the archive's `catalog.json` and profiles,
    so an archive's bytes are unchanged by the migration and no adopter contract changes. The source keeps the list.
15. Every existing string is migrated to a list in the same story, split at sentence ends. The migration is proven
    by building a release before and after it and comparing the archives' catalog and profile bytes.

For V26-3703, V26-3700 and V26-3701:

16. `lead-accept --take` keeps its behaviour. Its help text and run-delivery's SKILL.md say what it is for: a file
    the lead narrowed out of the lane's claims after the lane handed off. Handoff's `writer_not_owned` refusal names
    the `lead-own --grant` that clears it before review, which is the path for an out-of-lane file before hand-off.
17. The three untested refusals and the two untested outputs get pins: `--take` on a merged story, `--take` and
    `--own-co-changed` on a legacy record with nothing written, `--own-co-changed` with nothing to add making no
    commit, and the named-only warning's last line with the flag given.

For V26-3616:

18. An integration step's target peers are the registry's live deliveries whose base equals this delivery's
    `meta.integration.target`. `peer_deliveries` gains a target mode beside its same-base mode, and nothing else
    about the registry changes.
19. `doctor --delivery`, `lead-integrate` and the supervisor's status line name each target peer with what its queue
    is checking and has queued. With no target peer, or no integration target, nothing is shown and nothing changes.
20. The supervisor journals `target.settled` once a target peer's merge is followed by a quiet window with nothing
    checking or queued across every target peer. The window defaults to 10 minutes and `lead-wait --target-quiet
    SECONDS` sets it. `target.settled` joins `WAIT_KINDS`, so the lead's wait wakes on it. A restarted supervisor
    journals nothing twice, keyed by the last peer merge it saw. The tracker publisher posts nothing for it.
21. The guide's integration recipe says to wait for the target to settle before a review round or a full-suite run.

## Out of scope

- Declaring `regenerable.shared` in this repository's own binding, and in Athena's. Each waits for a release that
  holds the member.
- Verifying generated files nobody declared shared, and changing how `regenerable.paths` are regenerated at sync.
- An order between deliveries, and any refusal based on a busy target.
- A release tag. The new binding member and journal kind go out as a release candidate, cut separately.

## Approval Handoffs

- The operator approves this plan, with its decisions, before `lead-setup`.
- The operator names the delivery base: an integration branch with `main` as its target, or `main`.
- The operator decides whether an epic holds the six items. `lead-setup` reads the stories from it, and each item
  needs an Acceptance Criteria section for `lead-setup` to draft its criteria: V26-3703, V26-3700 and V26-3701 have
  none, and V26-3741's is headed "Acceptance". Adding them is a tracker change the operator approves.
- The operator approves the change request that carries the work to `main`.

## Execution posture and sensors

Every story is test-first except V26-3611, which is characterization-first: build a release, record the archive's
catalog and profile bytes, migrate, and prove the bytes equal.

A lane runs its story's test modules through `scripts/run-tests.py -p <module>`, once per module, since the script
keeps only the last `-p`, and the two gates. It never runs the full suite. A row that asserts new behaviour is
proven red against the code before its change. A row that asserts behaviour kept is proven red against an
over-correcting mutant, reverted afterwards. The mutants include: every regenerable path read as shared; the
candidate's binding read for `shared`; verification that skips the compare; a cached verification reused at another
commit; the listing guard skipped for a shared listing; a target peer read from the same base; a settled target
journalled while a peer still has a candidate queued; and a list joined with no separator. A wording deliverable has
a presence pin, proven by removing the phrase. A proof that mutates a file `provenance.lock.json` covers refreshes
that file's digest with the mutation.

V26-3741 adds a binding member and V26-3616 a journal kind. Search the tests for literal lists and counts of binding
members and journal kinds, and update those pins in the same story. Kinds are listed in
`skills/orchestrate-work/references/schemas.json`, `skills/run-delivery/scripts/tracker_publisher.py`,
`skills/delivery-views/assets/line-map.js` and the guide, and `target.settled` gets a row in
`tests/delivery_views_page/line-map-kinds.test.mjs` for its label and one for what the publisher does with it. A new
test module is catalogued in `.agents/orchestration.json` and `tests/test_own_deliveries.py`.

A verification row uses a fixture regenerator that is a short Python script writing a file from its inputs, so a row
starts one process per regeneration. A row that needs a failing or slow generator gives it its own script and a
timeout under two seconds. A target-peer row reuses `tests/test_run_delivery_peers.py`'s `peer()` fixture with
`base` set to the first delivery's integration target, and drives the quiet window with a window of zero and a
clock the test controls, never a sleep.

The suite's time budget binds this delivery. A story that adds tests keeps the suite inside
`tests/suite-budget.json`, a new class that keeps to its own folders and processes is `run_quietly`, and a new case
reuses a fixture its class already builds.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3611, V26-3703, V26-3700, V26-3701, V26-3741 | `catalog.json`, `profiles/`, `schemas/catalog.schema.json`, `schemas/profile.schema.json`, `agent_skills/validate.py`, `agent_skills/release.py`, `skills/orchestrate-work/scripts/merge_queue.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/adopting.md`, `tests/test_registry.py`, `tests/test_catalog.py`, `tests/test_delivery_views_hosted_projection.py`, `tests/test_lane_ruling_corpus.py`, `tests/test_lead_assign_owns.py`, `tests/test_handoff_listing.py`, `tests/test_merge_queue.py`, `tests/test_shared_outputs.py`, `docs/solutions/best-practices/a-listing-stop-is-cleared-by-ownership-not-a-base-merge-2026-11-16.md` |
| 1 | 2 | V26-3616 | `skills/orchestrate-work/scripts/delivery_state.py`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js`, `tests/test_run_delivery_peers.py`, `tests/test_lead_wait.py`, `tests/test_run_delivery_tracker.py`, `tests/delivery_views_page/line-map-kinds.test.mjs` |

Five files are shared by region. The lead grants each region in the record after `lead-init`, one line wider than
the edit at each end, and regrants it after a merge that edits inside it:

- `skills/run-delivery/scripts/run_delivery.py`: lane 2 holds the spans of `peer_deliveries`, `peer_line`,
  `lead_integrate`, `lead_wait`, the `Supervisor` class and the `Doctor` class's peers section. Lane 1 holds the rest,
  which includes ownership, review-prepare, review-reduce, handoff, `lead_accept`, `lead_assign` and the argument
  parser. Lane 2's `--target-quiet` option in the parser is a span the lead grants it.
- `skills/orchestrate-work/references/schemas.json`: lane 1 holds the binding's `regenerable` span, lane 2 the
  journal kinds' span.
- `skills/orchestrate-work/references/orchestration-guide.md`: lane 2 holds the integration recipe's span and the
  journal kinds table's row, lane 1 the rest.
- `tests/test_run_delivery.py`: lane 1 holds the span of `LaneStartAndReadinessTests`, lane 2 nothing unless the lead
  grants it.
- `.agents/orchestration.json`: lane 1 holds the test catalogue's span for `tests.test_shared_outputs`.

Every story that changes a file under `skills/` refreshes `provenance.lock.json` with the provenance writer and adds
its sentence to the catalog entry and each selected profile, as list items once V26-3611 has merged. No story adds
or removes a listed output.

## Stories

### V26-3611. Keep each catalog and profile sentence on its own line, so stories stop conflicting there

Lane 1, tier 1, not blocked. Characterization-first. Files: `catalog.json`, `profiles/`,
`schemas/catalog.schema.json`, `schemas/profile.schema.json`, `agent_skills/validate.py`, `agent_skills/release.py`,
`tests/test_registry.py`, `tests/test_catalog.py`, `tests/test_delivery_views_hosted_projection.py` and
`tests/test_lane_ruling_corpus.py`.

Found on `main`: `degradedBehavior` is `{"type": "string"}` in `schemas/catalog.schema.json`, and
`schemas/profile.schema.json` doesn't declare `description` at all. `agent_skills/validate.py` checks no field of
either, but requires canonical serialization, so a JSON string can't be split across lines. `agent_skills/release.py`
re-serializes both into the archive. The only field readers are `tests/test_delivery_views_hosted_projection.py`,
which looks for one sentence in each, and `tests/test_lane_ruling_corpus.py`, which whitespace-normalizes
orchestrate-work's. Run-delivery's `degradedBehavior` is about 10,900 characters and the linear profile's
`description` about 11,300, each one line.

Scenarios:

- two branches that each add a sentence to the same skill's entry merge without a conflict;
- `scripts/validate-registry.py` accepts the list form and the string form, and refuses an empty list and an empty
  item;
- a release built before and after the migration carries byte-identical `catalog.json` and profiles;
- the two field readers find their sentences in the list form.

### V26-3703. Say when `lead-accept --take` applies, and which grant clears an out-of-lane file before hand-off

Lane 1, tier 1, after V26-3611. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md` and `tests/test_lead_assign_owns.py`.

Found on `main`: `check_candidate_ownership` refuses a change outside the lane's claims at review-prepare,
review-reduce and handoff, so `--take` is reachable only after the lead narrows ownership post-handoff, as the
test's own setup says. SKILL.md says `--take` "accepts a change to a file outside the lane", and the help text says
it grants "a whole-file claim on a file the candidate changes outside its owns". Neither mentions that hand-off
already blocks this.

Change the help text, SKILL.md's row and handoff's `writer_not_owned` refusal by Decision 16.

Scenarios:

- handoff's refusal for an out-of-lane file names the `lead-own --grant` for that file and lane;
- `--take`'s help text and SKILL.md's row say it is for a file narrowed out after hand-off, each with a presence pin.

### V26-3700. Pin `--take`'s path and merged refusals, `--own-co-changed` with nothing to add, and the named-only warning's last line

Lane 1, tier 1, after V26-3703. Test-first. Files: `tests/test_lead_assign_owns.py`.

Found on `main`: `test_lead_assign_owns.py` covers `--take`'s path refusal among its others, but not its merged
refusal ("{key} has merged: --take grants a file to a candidate waiting for acceptance"). With
`--own-co-changed`, a named-only warning ends with no closing instruction, which no row holds, and no row runs the
flag with nothing to add.

Scenarios, by Decision 17:

- `--take` on a merged story is refused with that message and nothing written;
- `--own-co-changed` with nothing outside the lane makes no commit and leaves the record's bytes unchanged;
- with the flag, the named-only warning's last line is held exactly.

### V26-3701. Pin `--own-co-changed`'s and `--take`'s legacy-record refusals, with nothing written

Lane 1, tier 1, after V26-3700. Test-first. Files: `tests/test_lead_assign_owns.py` and `tests/test_run_delivery.py`.

Found on `main`: `lead-assign --own-co-changed` refuses a record without `laneOwnership` ("seed this record with
lead-own --seed first"), and so does `--take`. Neither refusal has a row. `test_run_delivery.py`'s
`test_legacy_record_uses_config_until_explicitly_seeded` already builds a legacy record by popping `laneOwnership`.

Scenarios, by Decision 17, each on that legacy setup:

- `lead-assign --own-co-changed` is refused with the seed message, and `state.json` and the journal are unchanged;
- `lead-accept --take` is refused likewise.

### V26-3741. Let lanes share generated files when the runner can reproduce them

Lane 1, tier 1, after V26-3701. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/references/adopting.md`, `tests/test_shared_outputs.py`, `tests/test_handoff_listing.py`,
`tests/test_merge_queue.py`, `.agents/orchestration.json`, `tests/test_own_deliveries.py` and
`docs/solutions/best-practices/a-listing-stop-is-cleared-by-ownership-not-a-base-merge-2026-11-16.md`.

Found on `main`: shared outputs exist only as listings. `shared_outputs` is the set of `regenerable.listings[].path`,
when `regenerators` is non-empty, and they are exempt from whole-file claims in `candidate_ownership`, `owned_by`,
`claims_report` and the overlap check, and refused by `lead-own --grant`, region grants and `--take`. Any other
regenerable path, such as Athena's graph, needs a claim, which is why one Athena delivery moved ownership of seven
generated paths 516 times. At sync, `run_pass` runs the regenerators and `settle` commits up to two passes; nothing
compares a candidate's bytes with a rerun on its own tree. The queue reads the binding from the base head and a lane
from `remote/base`, but the lead reads its working tree's. The solution note names `check_listing`, which is now
`unowned_listing_changes` and `listing_change`.

Add the member, verification and stops by Decisions 5 to 12, document the binding with a graph example in the guide
and adopting.md, and correct the solution note's function names.

Scenarios, each with a fixture binding whose regenerator builds `gen/graph.json` from `src/`:

- two lanes edit unrelated sources that change `gen/graph.json`, both reach handoff with no claim on it, and both
  merge, the second after the queue regenerates and verifies it on the moved base;
- after all reviewers approve, review-reduce and handoff for both lanes pass with no ownership move, at the same
  head and with verification run once for that head;
- a hand edit to `gen/graph.json` that the generator doesn't produce stops review-prepare, handoff and the queue
  with `shared_output_irreproducible`, naming the path;
- a generator that exits non-zero, and one past its timeout, stop handoff with `shared_output_generator_failed`,
  naming the argv and exit, and leave no worktree behind;
- a candidate that adds `gen/graph.json` to `regenerable.shared` in its own binding gets no exemption: handoff
  refuses its change to the file as outside its claims;
- a candidate that changes the generator script is refused as outside its claims, whatever `shared` matches;
- `binding_errors` refuses a `shared` pattern that matches no regenerable path, the binding file, or a file a
  regenerator's argv names;
- a shared listing still refuses an added output from a lane that doesn't own it alone, and passes a digest-only
  refresh;
- `lead-accept` accepts a hand-off whose only out-of-lane change is a verified shared output, verifies again at a
  newer head, and `--take` refuses the shared output;
- a binding without `shared` keeps every existing listing, handoff and queue row green, unchanged.

### V26-3616. Tell an integration lead when other deliveries are merging into its target

Lane 2, tier 1, after V26-3611. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/run-delivery/scripts/tracker_publisher.py`,
`skills/delivery-views/assets/line-map.js`, `tests/test_run_delivery_peers.py`, `tests/test_lead_wait.py`,
`tests/test_run_delivery_tracker.py` and `tests/delivery_views_page/line-map-kinds.test.mjs`.

Found on `main`: `peer_deliveries` is the registry's only reader, and it keeps an entry only when its base equals
this delivery's base. Every consumer, from doctor's peers section to the supervisor's `peer.merged` and the peer
waits, goes through it. `lead-init` seeds `meta.integration = {base, target, remote}` when `integrationTarget` is
set, and `lead_integrate` journals the integration's kinds, but nothing reads peers whose base is the target.
`WAIT_KINDS` doesn't include `peer.merged`, and has no kind for a target settling.

Add the target peers, their listing, `target.settled` and the guide's sentence by Decisions 18 to 21.

Scenarios, with this delivery's integration target as a peer delivery's base:

- `doctor --delivery`, `lead-integrate` and the supervisor's status line name the target peer with what its queue is
  checking and has queued;
- a peer on this delivery's own base is not listed as a target peer, and a target peer is not listed as a same-base
  peer;
- after the target peer merges and its queue empties, `target.settled` is journalled once after the window, across
  two supervisor passes and across a restart between them;
- while any target peer has a candidate checking or queued, nothing is journalled;
- `lead-wait` wakes on `target.settled`, and `--target-quiet` sets the window;
- a delivery with no integration target, and one whose target has no peer, show and journal nothing;
- the line map shows `target.settled`'s label, and the tracker publisher posts nothing for it;
- the guide's integration recipe holds its sentence, with a presence pin.
