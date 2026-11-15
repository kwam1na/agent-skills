---
reviewSlug: deliveries-on-a-shared-base
---

# Keep deliveries on a shared base out of each other's way

Epic V26-3512. Its five children were filed under V26-3305, whose own delivery merged to `main` on 2026-11-15 with the
seven items that correct what the queue and the close-out report. These five add mechanisms: V26-3319, V26-3318,
V26-3425, V26-3264 and V26-3320. Each was checked against `main` at `0879492` on 2026-11-14; each section says what
that check found, and V26-3305's delivery changed none of the code they name.

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
4. Three lanes work, and share text files by region, as V26-3305's delivery did. The lead grants each region in the
   record after `lead-init`, and regrants one after each merge that edits inside it. Lane 3 starts when V26-3318 has
   merged, since its story builds on the folder that story creates.
5. Every file a story creates has its exact path in the ownership table. No story edits `catalog.json` or
   `profiles/`: the lead writes that wording once, on the integration branch, fitted to the skills each profile
   ships.
6. This delivery does not declare `noDeferrals`: the installed release does not read it.
7. No story changes this repository's own binding, `.agents/orchestration.json`. The installed release refuses a
   binding member it does not know, and the binding is restored to `main`'s at integration. Every row uses a
   fixture binding. Declaring the new members here waits for a release that holds them, and is not this delivery's.

For V26-3319:

8. `selection.readers` keeps its meaning: for each path, the checks a change there adds. It does not say what a
   check reads in all. A readers entry may declare `"complete": true`: its paths are then everything its checks
   read, the checks' own test modules among them. A check's read set is known only when every readers entry that
   names it is complete, and is then the paths of all of those entries together.
9. A binding may also declare `selection.unread`: patterns for paths that no check reads.
10. The files a base move changed are listed without rename detection, so a rename counts as a path removed and a
    path added, and are matched with the binding's own pattern rule. The binding file itself counts as read by
    every check, and never matches `selection.unread`.
11. For a base move, a selected check is carried, with no re-run, when no file the move changed is in the
    candidate's diff and either every such file matches `selection.unread`, or the check's read set is known and
    no such file matches it. Every other selected check runs again. So does every `alwaysRun` check, unless every
    changed file matches `selection.unread`.
12. When at least one check is carried, the queue merges the new base into the synced head and journals one
    `check.carried`, naming the checks carried and the base commits they were not checked on. A candidate whose
    every check runs again is journalled as today.
13. A base move that conflicts with the candidate stops it as today. A batch is never carried. A re-sync in which
    at least one check was carried does not count against `run`'s one retry.

For V26-3318:

14. One lock for each base lives beside the repository's git common directory, at
    `<git-common-dir>/agent-skills/merge-slot/<base>.lock`, the folder resolved to an absolute path. In the base's
    name `%` is written `%25` and then `/` is written `%2F`.
15. The slot is taken once for each queue command, before the delivery's own `queue.lock`, and held until the
    command ends, however it ends: a merge, a stop, a refusal or an error. `serve` runs one command a turn, so it
    lets the slot go between turns. `lead-push`, `lead-sync` and `lead-upgrade-writer` take the slot the same way,
    before `queue.lock`. A post-merge `fullHealth` run does not hold it.
16. A queue command that finds the slot held waits for it within the binding's check timeout. Past that, it leaves
    its candidate at `queued`, journals nothing and stops nothing, and `serve` tries on a later turn. A lead
    command that finds it held past its own wait refuses and says which delivery holds it.
17. Only the lock says whether the slot is held, and the operating system releases a dead holder's. A holder file
    beside the lock names the holder's delivery, its pid and when it took the slot, for display only.
18. A queue command that waits writes a file naming its pid in a `waiting` folder beside the lock, and removes it
    when it takes the slot or gives up. A queue that has just merged and finds a waiter's file whose pid is alive
    waits one poll before it takes the slot again, and never more than one. Whoever reads a waiter's file whose
    pid is dead removes it.

For V26-3425:

19. A binding names how a failed `fullHealth` is re-checked, in a new optional member, `fullHealthRerun`:
    a `failurePattern` whose first group is a failing test's name, applied to every match; an `argv` that holds
    `{tests}`, which becomes one argument for each test; a `ranPattern`, which the rerun's output matches when it
    ran a test; and an optional `unrecheckedPattern`, for a failure the rerun cannot re-check, such as a unit that
    crashed or timed out. The patterns read a run's whole output. Binding validation refuses an `argv` without
    `{tests}`, a `failurePattern` with no group, a member with no `ranPattern`, and any of the three patterns that
    does not compile, naming the member. A binding without the member behaves as today.
20. A failed `fullHealth` is flaky only when all of this holds: the binding declares the member;
    `unrecheckedPattern` matches nothing; `failurePattern` names at least one test; and one run of `argv` with
    those tests, in the checkout the failed run used, exits 0 with output that `ranPattern` matches. The supervisor then journals
    `base.flaky`, naming the tests and the head, and no `base.broken`. In every other case it journals
    `base.broken` as today. The rerun is run once.
21. A `base.flaky` result is that head's `fullHealth` result: by itself it pauses nothing and holds nothing at the
    close, and "Needs you" lists the flaky tests as an item that does not hold. It is not a pass: it does not
    journal `base.repaired`, so a `base.broken` that still stands from an earlier head keeps `serve` paused. The
    first run that passes after it journals `base.repaired` when a `fullHealth` break still stands, and
    `base.verified` when none does.
22. `lead-resolve full-health`, when the failed head is no longer the base head, says so and names what stands: a
    run running at the new head, a run due at the new head, a `base.broken` that still awaits `base.repaired`, or
    none of these.
23. The tracker publisher posts nothing for `base.flaky`: it shows in the record and the views.

For V26-3264:

24. The command is run-delivery's own: `lead-suite [--at COMMIT] [-- ARG...]`, the base head on the remote when no
    commit is named. It makes a detached checkout of the commit under the binding's work folder, runs the
    binding's `prepare` steps and then its `fullHealth` argv with the extra arguments appended, and removes the
    checkout. It runs under any posture and journals no `base.*` entry.
25. Its result is appended to the flow log, as one line of `<delivery>.suite.jsonl`: the commit, the result, the
    whole argv, the seconds it took, the tail of the output, the host's platform, and the version of the
    interpreter the argv ran, asked of the argv's first element in the detached checkout. Where that cannot be
    read, the line says so. The lead cites the line when marking a finish line; no finish line reads it by itself.
26. The orchestration guide's integration steps name the command for the lead's own suite, and this repository's
    `docs/own-deliveries.md` names it with `-- --budget tests/suite-budget.json`. That file also gives the
    `fullHealthRerun` member this repository will declare once a release holds it (Decision 7), which V26-3425
    writes there.

For V26-3320:

27. Live deliveries are registered in the folder Decision 14 names, at
    `<git-common-dir>/agent-skills/deliveries/<key>.json`: the key, the base, the lead's checkout, the state folder
    and the supervisor's pid. A supervisor writes or refreshes its delivery's entry each time it takes its lock,
    which also registers a delivery initialized before the registry existed. `lead-init` writes the entry with no
    pid when it starts no supervisor, and `lead-close` removes it. An entry with a pid that is dead is stale. An
    entry with no pid is listed as having no supervisor.
28. `doctor --delivery` and the built-in live view list the peers on this delivery's base, each with what its queue
    is checking and has queued, read from the peer's record through its entry. Both show the merge slot's holder
    and since when, from V26-3318's holder file.
29. A delivery claims shared files in its own record, with `lead-own --claim PATTERN`, as whole files only; a
    region or a line range is refused. A claim makes a peer's `lead-assign` and `lead-accept` warn when the story
    or candidate touches a file the pattern matches, naming the claiming delivery, its stories on the file and
    their stages. A claim of a peer that is stale, or has no supervisor, warns too, and says which. It refuses
    nothing, and sets no order between deliveries.
30. On each pass the supervisor reads its peers' journals through the registry. For a peer's `candidate.merged`
    that it has not handled and that touches a file one of this delivery's open stories names, it journals
    `peer.merged`, naming the peer, the story and the files. How far it has read each peer's journal is kept in the
    flow log, so a restarted supervisor journals nothing twice. The tracker publisher posts nothing for
    `peer.merged`.

## Out of scope

- A lock or a registry that spans machines.
- An order between deliveries, and claims on part of a file across deliveries.
- A stopped post-merge `fullHealth`, which records no result today.
- Carrying a base move in a batch, and a bound on how many base moves in a row one candidate is carried across.
- Declaring `fullHealthRerun`, `selection.unread` or a complete readers entry in this repository's own binding.
- A release tag. The lock change and the record's new kinds go out as a release candidate, cut separately.

## Approval Handoffs

- The operator approves this plan, with its decisions, before `lead-setup`.
- The operator approves the final integration's change request to `main`.

## Execution posture and sensors

Every story is test-first. A lane runs its story's test modules through `scripts/run-tests.py -p <module>` and the
two gates, and never the full suite. The lead selects each candidate's checks from the modules its section names.

A row that asserts new behaviour is proven red against the code before its change. A row that asserts behaviour
kept is proven red against an over-correcting mutant of the new code, reverted afterwards. The mutants include: a
`fullHealth` failure always read as flaky; a flaky result that repairs a standing break; every base move carried;
a check named by a plain readers entry carried; a read set taken from one of a check's entries; the slot never
taken; the slot let go only when a candidate
merges; the holder file read as the lock; a queue that waits while any waiter's file exists; a peer's warning
given whatever file is claimed; a peer's warning turned into a refusal; and a stale registry entry read as live. A
wording deliverable has a presence pin, proven by removing the phrase. A proof that mutates a file
`provenance.lock.json` covers refreshes that file's digest with the mutation.

V26-3319, V26-3425 and V26-3320 each add a journal kind, and V26-3319 and V26-3425 add binding members. Search the
existing tests for literal lists and counts of journal kinds and binding members, and update those pins in the same
story. The kinds are listed in `skills/orchestrate-work/references/schemas.json`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js` and the guide. Each
new kind has a row in `tests/delivery_views_page/line-map-kinds.test.mjs` that shows its label, and a row that holds
what the tracker publisher does with it.

A row that needs two deliveries on one base gives each its own lead checkout, as a linked worktree of one
repository, with its own state folder and its own fake forge. At least one slot row and one registry row run a
command from a subfolder of a linked worktree, and one row asserts that both deliveries resolve the same absolute
lock path. A row that needs a dead holder or a dead waiter starts a process of its own, records its pid and stops
it by that pid. A row that needs two checks to overlap has the first check wait on a file the test controls.

The suite's time budget binds this delivery. A story that adds tests keeps the suite inside
`tests/suite-budget.json`, a new test class that keeps to its own folders, ports and processes is `run_quietly`,
and a new case reuses a fixture its class already builds where it can.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3319, V26-3318 | `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/SKILL.md`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js`, `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_adopter.py`, `tests/test_lane_rows.py`, `tests/test_run_delivery_tracker.py`, `tests/delivery_views_page/line-map-kinds.test.mjs`, `docs/solutions/best-practices/select-by-what-reads-a-change-2026-11-08.md`, `docs/solutions/best-practices/one-merge-slot-for-each-base-2026-11-15.md` |
| 1 | 2 | V26-3425, V26-3264 | `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_run_delivery_supervisor.py`, `tests/test_own_deliveries.py`, `docs/own-deliveries.md` |
| 2 | 3 | V26-3320 | `skills/orchestrate-work/scripts/render_state.py`, `tests/test_orchestration_render.py`, `tests/test_run_delivery_peers.py` |

Three text files are shared by region. No lane holds a whole-file claim on any of them:

- `skills/run-delivery/scripts/run_delivery.py`: lane 1 holds the spans of `lead_sync`, `lead_upgrade_writer` and
  `lead_push`. Lane 3 holds the spans of `lead_init`, `lead_assign`, `accept_story`, `lead_close`, the `Supervisor`
  class and the `Doctor` class. Lane 2 holds the rest, which includes `needs_you`, the `fullHealth` functions,
  `lead_resolve` and the argument parser.
- `tests/test_run_delivery.py`: lane 1 holds the span of the lead-push and base-move classes, lane 3 the span of the
  setup-check class, and lane 2 the rest.
- `skills/orchestrate-work/references/orchestration-guide.md`: lane 2 holds the spans of the close-out and the
  integration steps, lane 3 the span that describes the live view and the setup check, and lane 1 the rest.

Three kinds of edit cross these lines, and the lead settles each when the lane reaches it, by a region granted in
the record: lane 2's and lane 3's rows for a new journal kind in lane 1's schema, publisher, line map and tests;
lane 3's `lead-own --claim` option in the argument parser, which is lane 2's; and lane 3's reading of the peers on
the supervisor's pass. A story that has to add lines at a span's edge, or outside its lane's spans, asks the lead
first, who widens the claim or moves it.

Every story that changes a file under `skills/` refreshes `provenance.lock.json` with the provenance writer. No
story adds or removes a listed output.

## Stories

### V26-3319. Re-sync instead of restarting a check when the base moved only in files it doesn't read

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js`,
`tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_adopter.py`,
`tests/test_lane_rows.py`, `tests/test_run_delivery_tracker.py`,
`tests/delivery_views_page/line-map-kinds.test.mjs` and
`docs/solutions/best-practices/select-by-what-reads-a-change-2026-11-08.md`.

Found on `main`: when the base moves during a check, `check()` and `merge()` in `merge_queue.py` stop the candidate
with `base_moved`, and `_run_one` runs sync, check and merge again in full. `merge_queue.py` never reads
`selection.readers`, which says which checks a changed path adds and not what a check reads in all.

Carry a base move by Decisions 8 to 13. Register `check.carried` wherever journal kinds are listed, and the two
binding members in the schema and the guide, whose readers wording says what a plain entry and a complete one
mean. A carried merge still passes the region check against the new base. Extend the solution note on selecting by
what reads a change.

Scenarios, each in `tests/test_merge_queue.py` unless it names another file:

- with a complete readers entry for each selected check, the base moves only in a file outside the candidate's
  diff and outside those entries: the candidate merges with no check run again, and one `check.carried` names the
  checks and the base commits;
- the base moves in a file one check's complete entry lists: only that check and the `alwaysRun` checks run again,
  and `check.carried` names the others;
- a check named only by a plain readers entry runs again when the base changes that check's own test module, and
  when the base changes any other file;
- a check named by one complete entry and one plain entry runs again on a base move that touches neither entry's
  paths;
- a check named by two complete entries runs again when the base moves in a file only the second lists, and is
  carried when the move touches neither;
- a candidate with one check whose read set is known and untouched and one check no readers entry names runs only
  the second and the `alwaysRun` checks again;
- a base move wholly inside `selection.unread` carries every check, the `alwaysRun` ones and one with an unknown
  read set too; a move with one file outside it does not;
- a `selection.unread` pattern that matches the binding file carries nothing when the base changes that file;
- a complete entry with a `**` pattern and a base change to a nested file runs that check again;
- a base move that renames a file out of a complete entry's path, and one that deletes a file under it, each run
  that check again;
- a base move that changes the binding file runs every check again;
- the base moves in a file the candidate changes without conflict: every check runs again, and no `check.carried`
  is journalled;
- a base move that conflicts stops the candidate as today;
- a binding with no readers mapping and no `selection.unread` runs every check again as today;
- a candidate in a batch is not carried;
- a carried re-sync followed by a second base move is still given `run`'s one retry;
- the merged head of a carried candidate holds the new base, passes the region check against it, and the merge is
  refused if the remote base moved again in between;
- binding validation refuses a `complete` that is not a boolean and a `selection.unread` that is not a list of
  patterns, in `tests/test_orchestration_state.py`;
- the line map shows `check.carried`'s label, and the tracker publisher's entry for it is pinned.

### V26-3318. Share one merge slot for each base across every delivery's queue on a machine

Lane 1, tier 1, after V26-3319. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/SKILL.md`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/run-delivery/scripts/run_delivery.py`,
`tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_run_delivery.py` and
`docs/solutions/best-practices/one-merge-slot-for-each-base-2026-11-15.md`.

Found on `main`: `lead-push`, `lead-sync` and `lead-upgrade-writer` take the delivery's own `queue.lock`, and a
queue holds that lock for a whole command. The lock is one delivery's, in its own state folder, so another
delivery's merge or push still moves the base under a running check.

Add the slot by Decisions 14 to 18, and write the solution note. The live view's and the setup check's display of
the holder is V26-3320's.

Scenarios. The fixture's binding declares no readers, so V26-3319's carry cannot stand in for the slot:

- delivery A's check waits on a file the test controls. While it waits, delivery B's queue is seen waiting for the
  slot: the holder file names A, and B has journalled no `check.started`. Once A is let go and merges, B syncs at
  A's merged head and merges, and B's journal holds no `check.retried`, no `check.carried` and no `base_moved` stop;
- A's candidate stops on a failed check, and another on a check that times out, while A's queue process stays
  alive: B's candidate takes the slot on its next turn and merges, and the holder file names B;
- with the lock free and the holder file naming a live process of the test's own under another delivery's name, a
  queue takes the slot at once, merges, and rewrites the holder file;
- a queue whose holder process is stopped by its pid takes the slot on its next turn;
- a queue that finds the slot held past its wait leaves its candidate at `queued`, with no `candidate.stopped` and
  no `base.broken`, and takes it on a later turn;
- a queue that just merged and finds a live waiter's file lets the waiter take the slot first;
- a waiter's file whose writer was stopped by its pid delays a queue that just merged by no more than one poll, and
  is removed; a waiter's file whose live writer never takes the slot delays it by one poll and no more;
- a queue command that gives up its wait removes its own waiter's file;
- a `lead-push` in one delivery waits while the other delivery's check runs and lands after it ends, as do
  `lead-sync` and `lead-upgrade-writer`; each, when the slot stays held past its wait, refuses naming the holder
  and pushes nothing;
- one delivery's `lead-push` beside its own queue's command over two candidates: both end, and neither waits on the
  other past the first one's work;
- the two deliveries run from two linked worktrees and resolve the same absolute lock path, one of them from a
  subfolder of its worktree;
- with one delivery, the queue's journal for a candidate is what it is today, and the delivery's own `queue.lock`
  is still taken;
- a base named `integrate/x` and one named `integrate%2Fx` use different lock files;
- a post-merge `fullHealth` run does not hold the slot: a peer's candidate syncs while it runs.

### V26-3425. Tell a fullHealth failure that passes alone from one that reproduces

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/references/schemas.json`,
`tests/test_run_delivery_supervisor.py`, `tests/test_orchestration_state.py`, `tests/test_own_deliveries.py` and
`docs/own-deliveries.md`.

Found on `main`: `run_full_health` runs the binding's argv once, and `record_full_health` journals `base.broken`
on any non-zero exit and `base.repaired` on the first pass after a failed run. `lead_resolve` reads only the base
head, and refuses with nothing to rerun when the failed head has moved.

Re-check a failed run by Decisions 19 to 21, say what stands at a moved head by Decision 22, and register
`base.flaky` and the binding member where kinds and members are listed, in the spans the lead grants for it.

Scenarios, each with a fixture binding:

- a `fullHealth` whose one failing test passes alone journals `base.flaky` naming the test, no `base.broken`, and
  `serve` takes the next candidate;
- one whose failing test fails again alone journals `base.broken` as today;
- of two failing tests, the first the output names passes alone and the second fails again: `base.broken`;
- the matched test passes alone, but the run's output also holds a failure `unrecheckedPattern` matches:
  `base.broken`, and no rerun is started;
- a failed run in which `failurePattern` matches nothing journals `base.broken`;
- a rerun that exits 0 with output `ranPattern` does not match journals `base.broken`;
- the rerun runs in the checkout the failed run used, not in the lead's;
- a test that fails its one lone rerun journals `base.broken`, though a second rerun would pass;
- a failure named only past the last 2000 characters of the output is still found;
- a binding without `fullHealthRerun` journals `base.broken` and starts no second run;
- binding validation refuses an `argv` without `{tests}`, a `failurePattern` with no group, a member with no
  `ranPattern`, and a `failurePattern`, a `ranPattern` and an `unrecheckedPattern` that do not compile, each naming
  the member, in `tests/test_orchestration_state.py`;
- a `base.broken` from `fullHealth` at head A, then a run at head B whose failing test passes alone: `base.flaky`
  at B, no `base.repaired`, and `serve` stays paused; then a passing run at head C journals `base.repaired`, and
  `serve` takes the next candidate;
- a passing run after a `base.flaky` with no standing break journals `base.verified` and no `base.repaired`;
- a `base.flaky` head with no standing break does not hold the close, and "Needs you" lists the flaky test without
  holding;
- `lead-resolve full-health` with the failed head moved names, in turn: a run running at the new head; a run due
  there; a `base.broken` still standing, with `base.repaired`; and none of these. At the failed head itself it
  reruns as today;
- the runner's own output, from a real `scripts/run-tests.py` run over a failing test, an erroring test, a test
  that passes unexpectedly, a unit that crashes and a unit that times out, against the member
  `docs/own-deliveries.md` gives for this repository, read from that file: each failure either yields a name the
  `argv` reruns, or matches `unrecheckedPattern`, and a rerun that ran a test matches `ranPattern`;
- the line map shows `base.flaky`'s label, and the tracker publisher posts nothing for it.

### V26-3264. Run the lead's full suite in its own detached checkout of a commit

Lane 2, tier 1, after V26-3425. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_run_delivery_supervisor.py`,
`tests/test_own_deliveries.py` and `docs/own-deliveries.md`.

Found on `main`: `run_full_health` already makes a detached checkout, prepares it, runs the binding's argv and
removes it, but only for the supervisor's `full-health-run`, which runs nothing under `focused`, journals `base.*`
entries, and works in a temporary folder outside the work folder.

Add `lead-suite` by Decisions 24 to 26, sharing the checkout-and-run code with `full-health-run`.

Scenarios:

- `lead-suite --at COMMIT` followed by an edit in the lead's checkout while it runs: the result is the commit's,
  the checkout it ran in was under the binding's work folder, and it is gone afterwards;
- the `prepare` steps ran in that checkout before the argv did;
- the flow log's line holds the commit, the result, the argv with the extra arguments, the seconds, the output's
  tail and the platform;
- with a fixture argv whose first element is a relative path to a stub interpreter that exists only in the detached
  checkout and reports another version than the one running `lead-suite`, the line holds the stub's version; with
  one that reports none, the line says it could not be read;
- two runs leave two lines, the first unchanged;
- `lead-suite -- --budget F` appends the arguments, and a run that exits 3 for its budget exits non-zero and writes
  its line with that result;
- it runs under the `focused` posture, and journals no `base.verified`, `base.broken` or `base.repaired`;
- a failing suite exits non-zero, still writes its line, and its checkout is gone afterwards;
- with no `--at`, it runs at the base head on the remote, not at the lead checkout's own head;
- a commit that does not exist is refused before any checkout is made;
- an interrupted run removes its checkout and stops what it started, by its own process group;
- `full-health-run`'s existing rows still pass;
- `docs/own-deliveries.md` names the command with `-- --budget tests/suite-budget.json`, pinned in
  `tests/test_own_deliveries.py`, and the guide's integration steps name it, pinned.

### V26-3320. Let deliveries on one base see each other and claim shared files

Lane 3, tier 2, after V26-3318. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/test_run_delivery_peers.py`, `tests/test_run_delivery.py` and `tests/test_orchestration_render.py`.

Found on `main`: nothing registers a delivery outside its own state folder. `lead_init` and `lead_close` register
nothing, the `Doctor` class has no peer section, and `lead_assign` warns only of this delivery's own lanes.

Add the registry, the peer listing, the claims and the peer-merge entry by Decisions 27 to 30, and register
`peer.merged` where kinds are listed, in the spans the lead grants for it.

Scenarios, with two deliveries on one fixture base, each in its own linked worktree, unless a row says otherwise:

- `doctor --delivery` in each lists the other, with what its queue is checking and has queued, and the built-in
  live view lists it too; one of the two commands runs from a subfolder of its worktree;
- a delivery alone on its base shows no peers and no warnings;
- a peer whose supervisor was stopped by its pid is listed as stale; after `lead-run --resume` it is listed as live
  with the new pid; `lead-close` removes the entry;
- a delivery initialized with no supervisor is listed as having none, and one initialized before the registry
  existed is listed once its supervisor starts;
- `lead-accept` on a candidate that touches a file the peer claims warns, naming the peer, its stories on the file
  and their stages, and still accepts; `lead-assign` warns likewise and still assigns;
- with the peer claiming file X, `lead-accept` and `lead-assign` for work that touches only file Y warn of nothing;
- `lead-own --claim` with a region or a line range is refused;
- a peer on another base is not listed, warns of nothing, and its merge journals no `peer.merged`;
- a stale peer's claim still warns, and the warning says the peer is stale;
- a peer's merge that touches a file one of this delivery's open stories names is journalled once as
  `peer.merged`, across two supervisor passes and across a supervisor restart between them;
- a peer's merge that touches a file only a merged or closed story of this delivery names journals nothing, and so
  does one that touches no story's file;
- a registry entry that cannot be read is shown as unreadable and stops no command;
- the merge slot's holder and since when show in `doctor --delivery` and the live view while a peer's check runs;
- the line map shows `peer.merged`'s label, and the tracker publisher posts nothing for it.
