---
reviewSlug: deliveries-on-a-shared-base
---

# Keep deliveries on a shared base out of each other's way

Epic V26-3305, second delivery. The first delivery, merged to `main` on 2026-11-15, took the seven children that
correct what the queue and the close-out report. This one takes the five that add mechanisms: V26-3425, V26-3264,
V26-3319, V26-3318 and V26-3320. Each was checked against `main` at `0879492` on 2026-11-14; each section says what
that check found, and the first delivery changed none of the code they name.

Start from `main` at `4d0cf62`, which has the verified `linear-v2.3.0` release installed. Deliver on the integration
branch `integrate/shared-base`, so delivery commits and the record's bookkeeping do not move `main`. The generation
that delivers this work is not the one that will contain it.

## Finish line

- Both plan lenses align.
- Each of the five stories meets its tracker criteria and lands on `integrate/shared-base` through the merge queue,
  or closes with its reason recorded.
- The integration reaches `main` through a reviewed change request. Before it does, the full local suite passes on
  macOS with Python 3.11 at the integration candidate, run with `--workers 4 --budget tests/suite-budget.json`, and
  the suite stays inside the budget.
- CI's Ubuntu and `cut-release` jobs pass for that push to `main`.

## Decisions

The operator approves these with the plan. Each answers a question a ticket leaves open.

1. An integration base, `integrate/shared-base`, with `main` as its integration target, set up with
   `lead-setup --integration`.
2. The base keeps the `focused` posture. The full suite runs once, at final integration, and is this delivery's gate.
3. Lanes may push and open draft change requests, and the lead may mark them ready.
4. Three lanes work, and share text files by region, as the first delivery did. The lead grants each region in the
   record after `lead-init`, and regrants one after each merge that edits inside it. Lane 3 starts when V26-3318 has
   merged, since its story builds on the folder that story creates.
5. Every file a story creates has its exact path in the ownership table. No story edits `catalog.json` or
   `profiles/`: the lead writes that wording once, on the integration branch, fitted to the skills each profile
   ships.
6. This delivery does not declare `noDeferrals`: the installed release does not read it.

For V26-3425:

7. A binding names how a failed `fullHealth` is re-checked, in a new optional member, `fullHealthRerun`, with a
   `failurePattern` whose first group is a failing test's name, and an `argv` holding `{tests}`. A binding without
   it behaves as today.
8. When a `fullHealth` run fails and the binding declares the member, the supervisor runs the failing tests alone,
   once, in the same checkout. If every one passes, it journals `base.flaky`, naming the tests and the head, and no
   `base.broken`; `serve` is not paused. If any fails again, or the pattern finds no test, it journals
   `base.broken` as today.
9. A `base.flaky` result is that head's `fullHealth` result for the close: it holds nothing. "Needs you" lists the
   flaky tests as an item that does not hold the close.
10. `lead-resolve full-health`, when the failed head is no longer the base head, says so and names what stands:
    a run running or due at the new head, or a `base.broken` that still awaits `base.repaired`.

For V26-3264:

11. The command is run-delivery's own: `lead-suite [--at COMMIT]`, the base head when no commit is named. It makes
    a detached checkout of the commit under the binding's work folder, runs the binding's `prepare` steps and its
    `fullHealth` argv there, and removes the checkout. It runs under any posture and journals no `base.*` entry.
12. Its result goes to the flow log, as one line of `<delivery>.suite.jsonl`: the commit, the result, the argv, the
    interpreter's version, the host's platform, the seconds it took and the tail of the output. The lead cites that
    line when marking a finish line; no finish line reads it by itself.
13. The orchestration guide's integration steps and this repository's `docs/own-deliveries.md` name the command for
    the lead's own suite.

For V26-3319:

14. A base move is carried, with no check re-run, only when the binding declares `selection.readers`, every one of
    the candidate's selected checks is named by at least one readers entry, and no file the base changed is in the
    candidate's diff or matches a path of those entries. The queue then merges the new base into the synced head
    and journals `check.carried`, naming the base commits it did not check on.
15. A selected check that no readers entry names has an unknown read set, and is run again. So is a check whose
    readers a base change touches. The checks in `alwaysRun` always run again. Only the checks that have to run
    again do.
16. A base move that conflicts with the candidate stops it as today. A batch is never carried. A carried re-sync
    does not count against `run`'s one retry.

For V26-3318:

17. One lock for each base lives beside the repository's git common directory, at
    `<git-common-dir>/agent-skills/merge-slot/<base>.lock`, with each `/` in the base's name written `%2F`. It is
    taken as well as each delivery's own `queue.lock`, and before it.
18. A queue holds the slot from syncing a candidate until it merges or stops. `lead-push`, `lead-sync` and
    `lead-upgrade-writer` take it too. A post-merge `fullHealth` run does not hold it.
19. A queue that finds the slot held waits for it within the candidate's check timeout, then puts the candidate
    back to `queued` and tries on a later turn; nothing is stopped or journalled as broken for it. `serve`'s
    checking panel says which delivery holds the slot.
20. The operating system releases a dead holder's lock, so the next waiter takes the slot. A file beside the lock
    names the holder's delivery and pid, for display only.
21. A queue that has just merged lets a waiting queue go first: waiters write a file in a `waiting` folder beside
    the lock, and a queue that merged and finds one there waits one poll before taking the slot again.

For V26-3320:

22. Live deliveries are registered in the same folder, at `<git-common-dir>/agent-skills/deliveries/<key>.json`:
    the key, the base, the lead's checkout, the state folder and the supervisor's pid. `lead-init` writes it and
    `lead-close` removes it. An entry whose supervisor is dead is stale and is shown as stale.
23. `doctor --delivery` and the built-in live view list the peers on this delivery's base, each with what its queue
    is checking and has queued, read from the peer's record through its entry.
24. A delivery claims shared files in its own record, with `lead-own --claim PATTERN`, as whole files only. A claim
    makes a peer's `lead-assign` and `lead-accept` warn, naming the claiming delivery, its stories on the file and
    their stages. It refuses nothing, and sets no order between deliveries.
25. On each pass the supervisor reads its peers' journals through the registry. For a peer's `candidate.merged`
    that it has not seen and that touches a file one of this delivery's open stories names, it journals
    `peer.merged`, naming the peer, the story and the files.

## Out of scope

- A lock or a registry that spans machines.
- An order between deliveries, and claims on part of a file across deliveries.
- A stopped post-merge `fullHealth`, which records no result today.
- Carrying a base move in a batch.
- A release tag. The lock change and the record's new kinds go out as a release candidate, cut separately.

## Approval Handoffs

- The operator approves this plan, with its decisions, before `lead-setup`.
- The operator approves the final integration's change request to `main`.

## Execution posture and sensors

Every story is test-first. A lane runs its story's test modules through `scripts/run-tests.py -p <module>` and the
two gates, and never the full suite. The lead selects each candidate's checks from the modules its section names.

A row that asserts new behaviour is proven red against the code before its change. A row that asserts behaviour
kept is proven red against an over-correcting mutant of the new code, reverted afterwards. The mutants include: a
`fullHealth` failure always read as flaky; every base move carried; a carried check never re-run when its readers
are touched; the slot taken in place of `queue.lock`; a peer's warning turned into a refusal; and a stale registry
entry read as live. A wording deliverable has a presence pin, proven by removing the phrase. A proof that mutates a
file `provenance.lock.json` covers refreshes that file's digest with the mutation.

V26-3425, V26-3319 and V26-3320 each add a journal kind, and V26-3425 a binding member. Search the existing tests
for literal lists and counts of journal kinds and binding members, and update those pins in the same story. The
kinds are listed in `skills/orchestrate-work/references/schemas.json`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js` and the guide.

Rows that need two deliveries on one base build both in one fixture, each with its own state folder and its own
fake forge, in one repository whose git common directory they share. A row that needs a dead holder starts a
process of its own, records its pid and stops it by that pid.

The suite's time budget binds this delivery. A story that adds tests keeps the suite inside
`tests/suite-budget.json`, a new test class that keeps to its own folders, ports and processes is `run_quietly`,
and a new case reuses a fixture its class already builds where it can.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3319, V26-3318 | `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/SKILL.md`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js`, `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_adopter.py`, `tests/test_lane_rows.py`, `tests/test_run_delivery_tracker.py`, `tests/delivery_views_page/line-map-kinds.test.mjs`, `docs/solutions/best-practices/one-merge-slot-for-each-base-2026-11-15.md` |
| 1 | 2 | V26-3425, V26-3264 | `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery_supervisor.py`, `tests/test_own_deliveries.py`, `docs/own-deliveries.md`, `.agents/orchestration.json` |
| 2 | 3 | V26-3320 | `skills/orchestrate-work/scripts/render_state.py`, `tests/test_orchestration_render.py`, `tests/test_run_delivery_peers.py` |

Three text files are shared by region. No lane holds a whole-file claim on any of them:

- `skills/run-delivery/scripts/run_delivery.py`: lane 1 holds the spans of `lead_sync`, `lead_upgrade_writer` and
  `lead_push`. Lane 3 holds the spans of `lead_init`, `lead_assign`, `accept_story`, `lead_close`, the `Supervisor`
  class, the `Doctor` class and the argument parser. Lane 2 holds the rest, which includes `needs_you`, the
  `fullHealth` functions and `lead_resolve`.
- `tests/test_run_delivery.py`: lane 1 holds the span of the lead-push and base-move classes, lane 3 the span of the
  setup-check class, and lane 2 the rest.
- `skills/orchestrate-work/references/orchestration-guide.md`: lane 2 holds the spans of the close-out and the
  integration steps, lane 3 the span that describes the live view and the setup check, and lane 1 the rest.

A story that has to add lines at a span's edge, or outside its lane's spans, asks the lead, who widens the claim or
moves it. V26-3320's new commands and tests go in files of its own where they can: its test module is new, and the
lane asks the lead for a region before it edits any other lane's file.

Every story that changes a file under `skills/` refreshes `provenance.lock.json` with the provenance writer. No
story adds or removes a listed output.

## Stories

### V26-3319. Re-sync instead of restarting a check when the base moved only in files it doesn't read

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js`,
`tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_adopter.py`,
`tests/test_lane_rows.py`, `tests/test_run_delivery_tracker.py` and
`tests/delivery_views_page/line-map-kinds.test.mjs`.

Found on `main`: when the base moves during a check, `check()` and `merge()` in `merge_queue.py` stop the candidate
with `base_moved`, and `_run_one` runs sync, check and merge again in full. `merge_queue.py` never reads
`selection.readers`.

Carry a base move by Decisions 14 to 16, and register `check.carried` wherever journal kinds are listed. A carried
merge still passes the region check against the new base. Extend the solution note on selecting by what reads a
change, which lane 1 asks the lead for when it reaches it.

Scenarios, each in `tests/test_merge_queue.py`:

- the base moves only in a file outside the candidate's diff and outside its checks' readers: the candidate merges
  with no check run again, and one `check.carried` names the base commits;
- the base moves in a file one selected check reads: only that check runs again, and the others are carried;
- the base moves in a file the candidate changes without conflict: every check runs again;
- a base move that conflicts stops the candidate as today;
- a binding with no readers mapping runs every check again as today, and so does a candidate with a selected check
  that no readers entry names;
- an `alwaysRun` check runs again after a carried move;
- a candidate in a batch is not carried;
- a carried re-sync followed by a second base move is still given `run`'s one retry;
- the merged head of a carried candidate holds the new base, and the merge is refused if the remote base moved
  again in between.

### V26-3318. Share one merge slot for each base across every delivery's queue on a machine

Lane 1, tier 1, after V26-3319. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/SKILL.md`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/run-delivery/scripts/run_delivery.py`,
`tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_run_delivery.py` and
`docs/solutions/best-practices/one-merge-slot-for-each-base-2026-11-15.md`.

Found on `main`: `lead-push`, `lead-sync` and `lead-upgrade-writer` take the delivery's own `queue.lock`, and a
queue holds that lock from sync through merge. The lock is one delivery's, in its own state folder, so another
delivery's merge or push still moves the base under a running check.

Add the slot by Decisions 17 to 21, and write the solution note. The live view's and the setup check's display of
the holder is V26-3320's.

Scenarios:

- two deliveries' queues run against one fixture base, each with a candidate: neither candidate stops with
  `base_moved` because of the other, and both merge;
- a `lead-push` in one delivery waits while the other delivery's check runs, and lands after it ends, as do
  `lead-sync` and `lead-upgrade-writer`;
- a queue whose holder process is stopped by its pid takes the slot on its next turn, and the holder file names the
  new holder;
- a queue that finds the slot held past its wait puts its candidate back to `queued`, with no `candidate.stopped`
  and no `base.broken`, and takes it on a later turn;
- a queue that just merged and finds a waiter lets the waiter take the slot first;
- with one delivery, the queue's journal for a candidate is what it is today, and the delivery's own `queue.lock`
  is still taken;
- a base named `integrate/x` and one named `integrate%2Fx` use different lock files;
- a post-merge `fullHealth` run does not hold the slot: a peer's candidate syncs while it runs.

### V26-3425. Tell a fullHealth failure that passes alone from one that reproduces

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_run_delivery_supervisor.py`,
`tests/test_own_deliveries.py` and `.agents/orchestration.json`.

Found on `main`: `run_full_health` runs the binding's argv once, and `record_full_health` journals `base.broken`
on any non-zero exit. `lead_resolve` reads only the base head, and refuses with nothing to rerun when the failed
head has moved.

Re-check a failed run by Decisions 7 to 9, say what stands at a moved head by Decision 10, and declare
`fullHealthRerun` in this repository's binding. Lane 2 asks lane 1, through the lead, to register `base.flaky`
in the files lane 1 owns, or the lead moves those spans for the story.

Scenarios:

- a `fullHealth` whose one failing test passes alone journals `base.flaky` naming the test, no `base.broken`, and
  `serve` takes the next candidate;
- one whose failing test fails again alone journals `base.broken` as today;
- of two failing tests, one passes alone and one fails again: `base.broken`;
- a failed run whose output the pattern matches nothing in journals `base.broken`;
- a binding without `fullHealthRerun` journals `base.broken` and starts no second run;
- a `base.flaky` head does not hold the close, and "Needs you" lists the flaky test without holding;
- `lead-resolve full-health` with the failed head moved and a run due at the new head names that run; with a
  `base.broken` still standing it names `base.repaired`; at the failed head itself it reruns as today;
- this repository's binding validates with the new member, and its pattern finds the test in the runner's own
  failure output.

### V26-3264. Run the lead's full suite in its own detached checkout of a commit

Lane 2, tier 1, after V26-3425. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_run_delivery_supervisor.py` and
`docs/own-deliveries.md`.

Found on `main`: `run_full_health` already makes a detached checkout, prepares it, runs the binding's argv and
removes it, but only for the supervisor's `full-health-run`, which runs nothing under `focused`, journals `base.*`
entries, and works in a temporary folder outside the work folder.

Add `lead-suite` by Decisions 11 to 13, sharing the checkout-and-run code with `full-health-run`.

Scenarios:

- `lead-suite --at COMMIT` followed by an edit in the lead's checkout while it runs: the result is the commit's,
  and the checkout it ran in is gone afterwards;
- the flow log's line holds the commit, the result, the argv, the interpreter's version and the platform;
- it runs under the `focused` posture, and journals no `base.verified`, `base.broken` or `base.repaired`;
- a failing suite exits non-zero and still writes its line;
- with no `--at`, it runs at the base head on the remote, not at the lead checkout's own head;
- a commit that does not exist is refused before any checkout is made;
- an interrupted run removes its checkout and stops what it started, by its own process group;
- `full-health-run`'s existing rows still pass.

### V26-3320. Let deliveries on one base see each other and claim shared files

Lane 3, tier 2, after V26-3318. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/test_run_delivery_peers.py`, `tests/test_run_delivery.py` and `tests/test_orchestration_render.py`.

Found on `main`: nothing registers a delivery outside its own state folder. `lead_init` and `lead_close` register
nothing, the `Doctor` class has no peer section, and `lead_assign` warns only of this delivery's own lanes.

Add the registry, the peer listing, the claims and the peer-merge entry by Decisions 22 to 25, and show the merge
slot's holder, which V26-3318 records, in the same two places. Lane 3 asks lane 1, through the lead, to register
`peer.merged` in the files lane 1 owns.

Scenarios, with two deliveries on one fixture base unless a row says otherwise:

- `doctor --delivery` in each lists the other, with what its queue is checking and has queued, and the built-in
  live view lists it too;
- a delivery alone on its base shows no peers and no warnings;
- a peer whose supervisor was stopped by its pid is listed as stale, and `lead-close` removes the entry;
- `lead-accept` on a candidate that touches a file the peer claims warns, naming the peer, its stories on the file
  and their stages, and still accepts; `lead-assign` warns likewise and still assigns;
- a peer on another base is not listed and warns of nothing;
- a peer's merge that touches a file one of this delivery's open stories names is journalled once as
  `peer.merged`, across two supervisor passes; a peer's merge that touches no such file journals nothing;
- a registry entry that cannot be read is shown as unreadable and stops no command;
- the merge slot's holder shows in `doctor --delivery` and the live view while a peer's check runs.
