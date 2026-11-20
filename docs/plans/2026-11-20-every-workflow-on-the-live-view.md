---
reviewSlug: every-workflow-on-the-live-view
---

# Show every delivery workflow on the live view

An epic delivered through `orchestrate-work` can be watched as it happens: the line map says what each piece is
doing, what it waits on, how its review went and what landed, and it updates within seconds of every write. Work
that runs any other way can't be watched at all. A ticket taken through `execute-work`, a plan written through
`plan-work`, a diagnosis through `diagnose-work`, or a request routed by `deliver-work` shows nothing until its
agent reports back.

The outcome: every delivery workflow is visible on the same live server, in the line map's visual language, from
the events the workflows already emit. There is one event vocabulary, the runtime's `run-event/2`. An epic's lanes
already emit it, so a run looks the same whether it runs alone or inside an epic, and an epic piece's bound run
updates live too.

Checked against `main` at `4035682f` on 2026-11-20:

- The live server is `render_state.py live` (`skills/orchestrate-work/scripts/render_state.py`, `serve_live` and
  `LiveView`). `LiveView.watch_key` stats only the record's own files. `LiveView.refresh` raises the generation only
  when the record's fingerprint changes, pushes before it re-renders, and returns nothing while there is no record.
  `_events` sends `event: document` only while it holds an encoded record. `render()` returns code 2 on a record it
  can't read, before any renderer runs.
- The binding's `state` object is closed: `schemas.json` gives `orchestration-binding/1` `state` no additional
  properties, and `parse_config` validates it. `state.liveHome` names a renderer by id. `render_state.py` sees
  renderers only as argv.
- The line map and the history read runs through the `runs` member of `.agents/delivery-views.json`, a file
  `render_state.py` never reads: `runs list --json`, `runs view <id> --json`, `runs artifact …`. The runtime's run view
  is `run-view/1`, read by `summarize_run_view` in `skills/delivery-views/scripts/render.py` from its sections,
  `work` and `activity-history` among them. Those sections also carry phases the runtime's own commands record
  (`prepare`, `verify`). `assets/run-summary.js` draws only review rounds. `line-map.js`'s `refreshRuns` re-fetches its
  page three seconds after a pushed document and every 30 seconds.
- The runtime (`product-runtime/`, 0.8.6, built from `v26-technologies/agent-delivery-harness`) keeps runs under the
  git common directory, `.git/managed-delivery/runs`, so every worktree writes to one store. `runs list --json` is
  `run-inventory/1` and names that `runsDir`. `runs capabilities --json` here reports `run-event/2` and
  `artifactCapture: true`. The store holds no runs: this repository's root instructions declare no run-event command,
  and `.agents/delivery-views.json` has no `runs`.
- `run.started` takes `host`, `workflow` (`{releaseId, profile}`, the release, not the skill) and an optional
  `ticket`, and makes the run the worktree's current run. It is refused with `run_already_current` while the worktree
  has an open current run, unless given `--force`, which records `displacedRunId`. A run starts exactly once,
  `run.ended` clears the current pointer, and nothing is appended after it. `activity.observed`, `wait.started` and
  `wait.resolved` each require `activityId`, `attemptId` and `candidateTreeSha`. `activity.observed` adds `phase`,
  `state` (`queued`, `running`, `waiting`, `completed`, `failed`, `interrupted`), `owner` and an optional `nextStep`.
  `wait.started` adds `waitId`, `waitingOn` (`human`, `agent`, `external`, `unknown`), `reason`, `nextAction` and
  `scope`. `gate.reported` carries `command`, `outcome` and `durationMs`.
- Lens attempts and executor verification already emit `activity.observed` with a `phase`
  (`skills/execute-work/references/run-observation-contract.md`).
- Only `execute-work`, `obtain-review` and `compound-learning` emit. `tests/test_run_event_emission_corpus.py` holds
  each kind to one owning skill (`EMITTED_KINDS`) and lets other skills name a kind only by reference
  (`REFERENCED_KINDS`), which is name-level. Its `REQUIRED_PHRASES` and `EMIT_POINT_LIST_CLOSE` pin `execute-work`'s
  unconditional `run.started` and `run.ended` sentences.
- `$compound-delivery-kernel` is named by `deliver-work`, `execute-work`, `plan-work`, the ticket executors and the
  ticket creators. `diagnose-work` and `orchestrate-work` don't name it. `orchestrate-work` runs each lane's ticket
  through `$execute-work`.
- `docs/solutions/design-patterns/runless-review-observations-2026-10-23.md`: a view never synthesizes a run or a
  reviewer report. A workflow that can't emit shows no run, never an invented one.

## Finish line

- Both plan lenses align.
- In a repository that declares the run-event command and `state.liveRuns`, invoking `plan-work`, `diagnose-work` or
  `execute-work` directly creates exactly one run. The live server's runs page shows it within seconds of each
  emission, with or without a delivery record: the workflow stage it is in, what it is doing, any wait and who it
  waits on, its gates, decisions, blockers, change request and review rounds, and how it ended. A request routed by
  `deliver-work` through several workflows is one run whose stages follow each other on its line.
- An epic lane's `execute-work` starts and binds its own run, as today. Its piece's run re-renders live on the line
  map with no record write.
- The live server serves with no delivery record, the runs page as its front page.
- A repository that declares neither stays exactly as it is: no emission, no runs page, every existing golden
  unchanged.
- No runtime change and no new event kind. The runtime stays 0.8.6.
- Every story has landed on `main` through the merge queue. The full local suite has passed on macOS with Python
  3.11 at the base head after the last one landed, run with `--workers 4 --budget tests/suite-budget.json`, inside
  the budget.
- This repository adopts it from a release candidate (V26-3669), and one directly invoked `execute-work` run and one
  `deliver-work` request through `plan` and `execute` are watched end to end on the live server before the final is
  tagged. Tagging is the operator's. The change is additive, a minor version.

## Decisions

1. **One vocabulary.** Every workflow reports through `run-event/2` with the kinds the runtime already accepts.
   Nothing is added to `orchestrate-work`'s journal kinds. A standalone workflow writes no delivery record.

2. **The kernel owns the run's lifecycle, and every workflow applies it.** `run.started`, `decision.recorded`,
   `blocker.recorded` and `run.ended` move from `execute-work`'s `EMITTED_KINDS` entry to
   `$compound-delivery-kernel`. Each kind stays owned once. `execute-work` keeps `ticket.read`, `posture.declared`,
   `gate.reported`, `pr.opened` and `review.round.closed`. `diagnose-work` gains a dependency on the kernel and
   applies its run lifecycle rule; `plan-work`, `execute-work` and `deliver-work` already apply the kernel and name
   the rule. The rule:
   - A workflow the operator invoked starts the run. So does an epic lane's `execute-work`, which binds its run with
     `bind-run` as today.
   - A workflow another workflow invoked in the same session joins the current run and starts none. `deliver-work`
     starts its run at `route`, and the workflows it routes to join it.
   - The run stays open across every approval or decision the request waits on, each marked by `wait.started` and
     `wait.resolved` with `waitingOn: human`, so a `deliver-work` request through `plan` and `execute` is one run and
     its approval shows as "Needs you" while the operator holds it.
   - Only the workflow that started a run ends it, when its request finishes (`complete`), stops on a blocker
     (`blocked`), or is stopped by the operator with work left (`partial`).
   - A request abandoned without an end leaves its run open. The next start in that worktree meets it as the open
     current run and starts with `--force`, which records it as `displacedRunId`. A start the runtime still refuses
     is silent, as Decision 4 says, and the workflow goes on without a run.

   `execute-work`'s unconditional sentences ("`run.started` before reading repository authority…", "`run.ended`
   last…") are replaced by references to the kernel's rule. This is a withdrawn behaviour, so every skill in the
   `linear` profile is searched by meaning for the unconditional start and end, and V26-3666 adds absence pins on that
   phrasing and its synonyms.

3. **Stage markers have their own identity.** At each workflow's entry and exit, the kernel has it emit
   `activity.observed` with `activityId: stage-<name>`, `phase: <name>`, `state: running` at entry and `completed`
   (or `failed` or `interrupted`) at exit, a new `attemptId` per entry, and `owner` the host. `<name>` is one of
   `route`, `plan`, `diagnose`, `execute`, `review` and `compound`. Where there is no candidate yet, the stage
   markers and waits carry the worktree's `HEAD^{tree}` as `candidateTreeSha`. The view reads stages only from
   entries whose `activityId` starts `stage-` and whose phase is one of those names. Lens attempts, executor
   verification and the runtime's command phases (`prepare`, `verify`) never become stages. The run's line shows
   each stage once, in the order of its first entry. The current stage is the innermost stage that has an entry with
   no later exit, so a nested `review` that exits hands the ring back to `execute`. A stage entered again after its
   exit is the current stage again, still drawn once. `wait.started` and `wait.resolved` mark every stop for an
   approval or a decision, with `waitingOn: human` where the operator is needed. "Needs you" is shown only for a
   `waitingOn: human` wait with no `wait.resolved` of the same `waitId`.

4. **Same capability rule.** Emission stays optional, declared by the root instruction file, and silent when absent,
   with no handoff and no blocker. A refused or failed emission changes no outcome. What is emitted is observability,
   not evidence, and nothing gates on it.

5. **The binding names the runs page and the store.** `orchestration-binding/1` `state` gains an optional
   `liveRuns`: `{"renderer": "<renderer id>", "inventory": {"argv": [...], "timeoutSeconds": N}}`. `renderer` names
   one of the binding's renderers, checked by `parse_config` as `liveHome` is. `inventory.argv` is the runtime's
   `runs` command, the same argv as `.agents/delivery-views.json`'s `runs`, to which `live` appends `list --json`.
   `timeoutSeconds` is 10 when absent. With no `liveRuns`, `live` never runs the inventory or reads the store.

6. **The live server watches the run store and pushes in a fixed order.** With `liveRuns`, `live` reads `runsDir`
   from the inventory once at start, bounded by its timeout, and adds the store to the watch key: the size and
   modification time of `runsDir`, its directories and their files, never opened or parsed. On a change of the
   store alone:
   1. the views are re-rendered and written;
   2. then the server raises a separate runs generation;
   3. then it sends `event: runs` with data `{"generation": N}` on every open `/events` stream.

   A record change still sends `event: document` as today, now after its views are written. `event: runs` is sent
   with or without a record. Pages reach `/events` only through the live adapter the server injects
   (`_LIVE_ADAPTER` in `render_state.py`), so V26-3667 adds `deliveryStateStore.onRuns(fn)` to it, calling `fn` with the
   event's data on each `runs` event. The adapter keeps `read`, `subscribe` and `onStatus` as they are. The runs page
   re-fetches itself through `onRuns`. The line map's `refreshRuns` re-fetches through it too, with no delay, keeping
   its 30-second interval. A page that registers no `onRuns` is unaffected. An
   inventory that fails, times out or names a `runsDir` that can't be read leaves the record's watch working. The
   runs page then says runs aren't being watched, from a flag the server passes to its views.

7. **No record is not an error.** `live` starts and serves when the state directory holds no record. With no
   record, it renders each live view with exactly `{"spec": "delivery-state-absent/1"}` on stdin, where `render()`
   today returns code 2. The `liveRuns` renderer is the front page while there is no record. Once a record reads
   cleanly, `liveHome` takes the front page back. The line map and the history, given that document, each render one
   page that says no delivery is under way and links to the runs page.

8. **A runs page in the line map's language.** `render.py runs` is a new live renderer, `runs.html`, built from the
   line map's typefaces, colour tokens and run summary.
   - "Now" lists open runs, newest first. Each has its line of stages, its current activity and next step, and "Needs
     you" under Decision 3's rule, with the wait's reason and next action. A wait on an agent or outside party shows
     its reason and next action without "Needs you".
   - Each run opens a detail panel: its gates (`command`, outcome, duration), decisions, blockers, change request,
     review rounds through the existing run summary track and cards, and result with cost unit and coverage.
   - Ended runs follow, newest first, and link to the history. A run that a later `run.started` names as its
     `displacedRunId` is listed with them, marked "Left open, replaced", never under "Now".
   - A run bound to a delivery's piece names that piece and links to it on the line map.

   The page reads runs only through the existing `runs` commands, under the existing deadline, scrub list and
   4 MiB budget, ended runs giving up their summaries first, oldest first.

9. **One standing server per repository.** Every `live` writes its own entry,
   `<git common directory>/delivery-live/<pid>.json`, at start, with its PID, host, port, the state directory it
   serves and whether it has `liveRuns`, and removes only that entry on a clean stop. An entry is verified when its PID is alive and its address
   answers `/health` as a live server for this repository
   (`docs/solutions/test-failures/a-ready-wait-that-accepts-any-listener-checks-another-runs-server-2026-11-09.md`).
   `live --if-absent` prints the address of a verified entry, preferring one with `liveRuns`, and exits 0; with none,
   it serves as `live` does. So a lead's delivery server and a standing server never remove each other's entry. The
   run store is shared across worktrees, so one server shows every worktree's runs. `live --stop` signals the PID of
   each verified entry this checkout's state directory started, and nothing else: an entry that fails verification
   is left alone, its PID never signalled. The kernel says: where the repository declares a live view command, a workflow that starts a run as
   the operator's invocation runs it with `--if-absent` in the background and gives the operator the address. A
   workflow that joins a run, and an epic lane, does neither. The lead keeps starting the delivery's live server as
   today.

10. **Characterize the runtime first, and keep what it shows.** Before any prose changes, V26-3666 runs the pinned
    0.8.6 runtime in a fixture repository. It records, as vectors, whether each planned emission is accepted:
    `run.started` without `ticket`, the stage markers and waits with `HEAD^{tree}` as candidate, `run.started` while
    another run is current with and without `--force`, and `emit` with no managed installation registered. It then
    drives these runs and stores the runtime's real `runs list --json` and `runs view <id> --json` output for each:
    - an ended `plan` then `execute` run with gates, a decision, a blocker, a change request, a nested `review` entry
      and exit, a lens attempt with `phase: review`, a human wait resolved, and `run.ended` with cost;
    - an open `execute` run inside a `plan` stage, with a runtime command that records a `prepare` or `verify` phase,
      a nested `review` entry and exit followed by more `execute` activity, and an unresolved `waitingOn: human` wait;
    - an open run whose `review` is entered, exits, and is entered again after more `execute` activity, still open;
    - an open run that re-enters `execute` after a `review` exit;
    - an open run waiting on an agent;
    - a run left open, then displaced by a `--force` start, and that start's run, ended `partial`.

    Each refusal, including a runtime command that can't record a phase in the fixture, goes to the operator as a
    decision before the story goes on. A runtime change belongs to the harness repository and is out of scope.

## Out of scope

- Any change to the runtime or its `run-event/2` grammar, including a change log for the live server.
- New journal kinds, and writing a delivery record for a standalone run.
- The hosted page and its snapshot. Runs stay on the local live server.
- Showing a lane's current activity and waits in an epic piece's detail panel beyond the live re-render. That is a
  follow-up.
- Changes to the history page beyond its no-record page and its links to and from the runs page.
- Ticket executors (`execute-linear-ticket`, `execute-jira-ticket`) beyond what they inherit from `execute-work`.
- A race between two simultaneous `live --if-absent` starts. The plan claims nothing about it.
- Windows, and any host other than Codex and Claude Code.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator approves creating the four tracker items. Approved and created on 2026-11-20 as V26-3666 to V26-3669.
- Any runtime refusal V26-3666 finds goes to the operator before V26-3666 continues (Decision 10).
- The operator grants the lanes `push` and `draftPullRequests`.
- The operator calls the release candidate V26-3669 adopts, and the final.

## Execution posture and sensors

Test-first, with V26-3666 characterization-first for the runtime. Sensors:

- `tests/test_run_event_emission_corpus.py`, for ownership, the lifecycle rule's pins and absence pins.
- `tests/test_run_event_acceptance.py`, new, for the runtime vectors and captured output.
- `tests/test_linear_qualification.py`, for corpus pins.
- `tests/test_orchestration_render.py`, which validates `liveHome` today, for `liveRuns`, `live` with no record, the
  runs watch, the push order and `--if-absent`.
- `tests/test_delivery_views_render.py` and `tests/test_delivery_views_history.py`, for the runs renderer and the
  no-record pages.
- `tests/delivery_views_page/`, through `tests/test_delivery_views_page.py`, for the page, with a new golden case
  under `tests/fixtures/delivery-views/runs/`.
- `tests/test_own_deliveries.py`, for each new test module's catalogue entry.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.

V26-3666's runtime tests run the stored runtime under Node 22.6 or newer in a temporary git repository, never in the
checkout. Where Node is missing or older, they fail naming the need, rather than skip, so the guard is never vacuous.
Live server tests bind `--port 0`, keep to their own folders and temporary git common directories, and stop only the
servers they started, by recorded PID. A class that waits on a real server process is marked `run_quietly`. New
tests reuse the existing live-server and fixture-repository builders and keep the suite inside
`tests/suite-budget.json`. No number in the budget moves.

## Scenarios

V26-3666, in `tests/test_run_event_acceptance.py`, against the stored runtime:

- Each acceptance vector in `tests/fixtures/run-events/acceptance.json` is re-run, so a later runtime that refuses
  one fails it. `run.started` while another run is current is refused without `--force` and accepted with it,
  recording `displacedRunId`.
- The captured `runs list --json` and `runs view <id> --json` in `tests/fixtures/run-events/` are re-derived from the
  same driven sequence and compared, apart from times and ids, so a runtime whose view changes shape fails it.
- The driven runs are evidence of what the runtime accepts and shows, not of what the skills say.

V26-3666, in `tests/test_run_event_emission_corpus.py`:

- `EMITTED_KINDS` lists the four lifecycle kinds under `skills/compound-delivery-kernel/SKILL.md`. A second owner of
  any kind fails.
- The kernel carries each clause of Decision 2's rule, the stage marker identity and names of Decision 3, and
  Decision 9's start-only server sentence, each pinned with its neighbouring text. Among them: the run stays open
  across a human wait within the request, only the starter ends it, and an abandoned run is displaced by the next
  start's `--force`.
- `execute-work`'s `REQUIRED_PHRASES` and `EMIT_POINT_LIST_CLOSE` pins on the unconditional sentences are replaced
  by pins on its reference to the kernel's rule, including that an epic lane's `execute-work` starts and binds its
  own run and does not start the live server.
- `deliver-work` carries that it starts the run at `route` and that the workflows it routes to join it.
- `diagnose-work` names `$compound-delivery-kernel` and its run lifecycle rule, not only the capability phrases.
  So does `plan-work`.
- Absence pins in `execute-work`, `plan-work`, `diagnose-work` and `deliver-work` on the unconditional start and end
  and their synonyms ("start the run", "begin a run", "emit `run.started` first", "`run.ended` last"), outside a
  reference to the kernel.

V26-3667, in `tests/test_orchestration_render.py`:

- A binding with `state.liveRuns` naming a declared renderer validates. One naming an undeclared renderer, or with an
  `inventory` without `argv`, is refused, naming the field.
- `live` over an empty state directory serves, writes its own `delivery-live/<pid>.json` entry, and its front page is the `liveRuns` renderer's
  output, rendered from exactly `{"spec": "delivery-state-absent/1"}`. When a record appears, the front page follows
  `liveHome`.
- Runs watch, over an empty state directory and again with a record: with a counting fixture inventory command and a
  fixture `runsDir`, an open `/events` stream, and a stub `liveRuns` renderer that writes the store's listing into its
  page. Appending a partial, non-JSON line to a file under `runsDir` produces exactly one `event: runs` within two
  poll intervals. A GET of the runs view made after that event returns the appended content, so the views were
  written first. With a record, no `event: document` is sent for that change.
- A record change sends `event: document` only after its views are written: a GET of a view made after the event
  returns the content the record change produced.
- The injected `_LIVE_ADAPTER`, run under `jsdom` against the running server, calls a registered `onRuns` once per
  `event: runs` with its data, and leaves `subscribe` uncalled for it.
- Without `liveRuns`, appending under the same `runsDir` runs the inventory command zero times and re-renders
  nothing.
- An inventory that exits non-zero, one that passes its timeout at start, or a `runsDir` removed while serving, leaves a record change still sending
  `event: document`, and the views receive the not-watched flag.
- `live --if-absent` with a verified entry prints its address and exits 0, starting nothing. With only a stale entry,
  a dead PID, or a listener that answers `/health` as another server, it serves itself.
- Two servers over different state directories, one over a record and one with `liveRuns` and no record: each
  writes its own entry, `--if-absent` prints the `liveRuns` one, and stopping either cleanly removes only its own
  entry, the other still verified. Then, with both up again, `live --stop` run for one server's state directory stops
  that server and removes its entry, while the other still answers `/health` as this repository's live server, its
  PID alive and its entry in place. The test then stops the second server by its recorded PID.
- `live --stop` stops a verified server this checkout's state directory started, and its entry goes.
- Refusal: an entry naming the PID of a sleeper the test started, which is not a live server, with an address that
  fails `/health` or answers as another server. `live --stop` exits without signalling, the sleeper is still alive,
  and the test then stops the sleeper by its recorded PID.

V26-3668, in `tests/test_delivery_views_render.py`. Every run in the inventory comes from V26-3666's captured bytes,
changed only in ids and times:

- The runs payload holds every captured run: the open `execute` run inside `plan` waiting on a human, the open run
  waiting on an agent, the two re-entry runs, the ended run whose human wait was resolved, the displaced run, and the
  `partial` run that displaced it. For each it
  carries the stages in order and the current stage, the current activity and next step, the wait, gates with
  `command` and `durationMs`, decisions, blockers, change request, rounds, and result with cost unit and coverage.
- The open `execute` run carries a runtime `verify` phase, a lens attempt with `phase: review`, and a nested `review`
  stage entry and exit followed by more `execute` activity. Its stages are exactly `plan`, `execute` and `review`,
  with `execute` current. `verify` and the lens attempt add none.
- The run that re-enters `review` has stages exactly `plan`, `execute`, `review`, each once in first-entry order,
  with `review` current. The run that re-enters `execute` after a `review` exit keeps that order, with `execute`
  current.
- Only the unresolved human wait yields "Needs you". The resolved one and the agent wait don't.
- The displaced run is listed with the ended runs, marked "Left open, replaced", and not under "Now".
- A run whose `view` fails or passes the deadline shows unavailable, and the others render. An inventory over the
  4 MiB budget drops the oldest ended runs' summaries first.
- A run bound to a record's piece carries that piece's key.
- `render.py runs`, `line-map` and `history` each render from exactly `{"spec": "delivery-state-absent/1"}`, exiting
  0. The runs page lists the inventory, and the line map and history each say no delivery is under way and link to
  the runs page.
- `live` over an empty state directory with the real `render.py runs` as the `liveRuns` renderer answers `/` with
  200 and the inventory's runs.

V26-3668 page rows, in `tests/delivery_views_page/runs.test.mjs`:

- "Now" lists open runs before ended ones. Each order is newest first, checked with two ended runs.
- "Needs you" appears only on the unresolved human wait. The agent wait shows its reason and next action without it.
- A run's line draws exactly its stages, `execute` ringed, and draws no `verify`. Each re-entry run draws each stage
  once, in first-entry order, with the re-entered stage ringed.
- The displaced run shows under ended runs as "Left open, replaced".
- The detail panel draws gates, decisions, blockers, the change request and the run summary's rounds.
- With the real `_LIVE_ADAPTER` injected, as the existing line-map test injects it, and driven with the exact
  `event: runs` bytes and re-fetch sequence V26-3667's server produces, the page re-fetches itself through `onRuns` and
  the open panel shows the new `gate.reported`. The line map re-fetches its runs through `onRuns` on the same event
  without the three-second delay.
- A new `runs` golden case pins the page whole. Every existing line-map and history golden renders unchanged.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3666 | `skills/compound-delivery-kernel/`, `skills/execute-work/`, `skills/plan-work/SKILL.md`, `skills/diagnose-work/`, `skills/deliver-work/SKILL.md`, `tests/test_run_event_emission_corpus.py`, `tests/test_run_event_acceptance.py`, `tests/fixtures/run-events/`, `tests/test_own_deliveries.py`, `.agents/orchestration.json` |
| 1 | 2 | V26-3667 | `skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_render.py` |
| 2 | 2 | V26-3668 | `skills/delivery-views/`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |
| 3 | 1 | V26-3669 | `AGENTS.md`, `docs/own-deliveries.md`, `.agents/delivery-views.json` |

V26-3668 follows V26-3666 for its captured run output and V26-3667 for the push it is driven with. V26-3669 keeps lane 1's
`.agents/orchestration.json` from V26-3666. Each lane updates the catalog entry, every selected profile and the
provenance digests in the same change as its skill edits, as the repository requires. V26-3669 runs once a release
candidate holding V26-3666 to V26-3668 is applied here.

## Stories

### V26-3666. Every delivery workflow reports its run in one vocabulary

Lane 1, tier 1, not blocked. Characterization-first for the runtime, then test-first. Files:
`skills/compound-delivery-kernel/SKILL.md`, `skills/execute-work/SKILL.md`,
`skills/execute-work/references/run-observation-contract.md`, `skills/plan-work/SKILL.md`,
`skills/diagnose-work/SKILL.md`, `skills/deliver-work/SKILL.md`, `tests/test_run_event_emission_corpus.py`,
`tests/test_run_event_acceptance.py`, `tests/fixtures/run-events/`, `tests/test_own_deliveries.py` and
`.agents/orchestration.json`.

Implement Decisions 2, 3 (emission side), 4 and 10, and the kernel's sentences of Decision 9. Catalogue
`tests/test_run_event_acceptance.py` in `tests/test_own_deliveries.py` and the binding's check catalogue.

### V26-3667. The live server serves without a record and follows the run store

Lane 2, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/render_state.py`,
`skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/SKILL.md` and
`tests/test_orchestration_render.py`.

Implement Decisions 5, 6 (with `onRuns` on the adapter), 7 (the server side) and 9 (`--if-absent`, `--stop` and
the `delivery-live/` entries).

### V26-3668. A live runs page in the line map's language

Lane 2, tier 2, after V26-3666 and V26-3667. Test-first. Files: `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/scripts/views_common.py`, `skills/delivery-views/assets/`, `skills/delivery-views/SKILL.md`,
`tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py`, `tests/delivery_views_page/` and
`tests/fixtures/delivery-views/`.

Implement Decisions 3 (reading side), 7 (the views' no-record pages) and 8, and the line map's re-fetch on
`event: runs`.

### V26-3669. Watch this repository's own runs

Lane 1, tier 3, after V26-3666, V26-3667 and V26-3668. Sensor-only. Files: `AGENTS.md`, `docs/own-deliveries.md`,
`.agents/delivery-views.json` and `.agents/orchestration.json`.

Declare the run-event command in `AGENTS.md`, `runs` in `.agents/delivery-views.json`, and the runs renderer and
`state.liveRuns` in the binding. Say in `docs/own-deliveries.md` how to start and stop the standing live server.
Watch one directly invoked `execute-work` run and one `deliver-work` request through `plan` and `execute` end to
end, and record what each showed.
