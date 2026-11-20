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
  `LiveView`). `LiveView.watch_key` stats only the record's own files (`changes.jsonl`, the state, the journal, the
  peer waits, the plan). `LiveView.read` returns nothing while there is no record, so with no delivery under way the
  server never renders.
- The line map and the history already read runs through the `runs` member of `.agents/delivery-views.json`:
  `runs list --json`, `runs view <id> --json`, `runs artifact …`. `assets/run-summary.js` draws only a run's review
  rounds. A piece shows its run once a lane has run `bind-run`. Nothing re-renders when a run changes.
- The runtime (`product-runtime/`, 0.8.6, built from `v26-technologies/agent-delivery-harness`) keeps runs under the
  git common directory, `.git/managed-delivery/runs`, so every worktree of the repository writes to one store.
  `runs list --json` is `run-inventory/1` and names that `runsDir`. `runs capabilities --json` here reports
  `run-event/2` and `artifactCapture: true`. The store holds no runs: this repository's root instructions declare no
  run-event command, and `.agents/delivery-views.json` has no `runs`.
- `run.started` takes `host`, `workflow` (`{releaseId, profile}`, the release, not the skill) and an optional
  `ticket`, and makes the run the worktree's current run. A run starts exactly once, and nothing is appended after
  `run.ended`. `activity.observed` carries a `phase` label, a `state`, an `owner` and an optional `nextStep`.
  `wait.started` carries `waitingOn` (`human`, `agent`, `external`, `unknown`), `reason` and `nextAction`.
- Only `execute-work`, `obtain-review` and `compound-learning` emit. `plan-work` and `diagnose-work` emit nothing.
  `tests/test_run_event_emission_corpus.py` holds each kind to exactly one owning skill (`EMITTED_KINDS`) and lets
  other skills name a kind only by reference (`REFERENCED_KINDS`). `execute-work` owns `run.started`,
  `decision.recorded`, `blocker.recorded` and `run.ended`.
- `docs/solutions/design-patterns/runless-review-observations-2026-10-23.md`: a view never synthesizes a run or a
  reviewer report. That holds here. A workflow that can't emit shows no run, never an invented one.

## Finish line

- Both plan lenses align.
- In a repository that declares the run-event command and `runs`, starting `plan-work`, `diagnose-work` or
  `execute-work` directly creates one run. The live server shows it on its runs page within seconds of each emission:
  which workflow it is in, what it is doing, any wait and who it waits on, its gates, decisions, blockers, change
  request and review rounds, and how it ended. A request routed by `deliver-work` through several workflows is one
  run whose phases follow each other on its line.
- An epic piece's bound run re-renders live on the line map, with no record write.
- The live server serves with no delivery record: the runs page is its front page until a delivery starts.
- A repository that declares neither stays exactly as it is: no emission, no runs page, every existing golden
  unchanged.
- No runtime change and no new event kind. The runtime stays 0.8.6.
- Every story has landed on `main` through the merge queue. The full local suite has passed on macOS with Python
  3.11 at the base head after the last one landed, run with `--workers 4 --budget tests/suite-budget.json`, inside
  the budget.
- This repository adopts it from a release candidate (KEY-4), and one directly invoked `execute-work` run here is
  watched end to end on the live server before the final is tagged. Tagging is the operator's. The change is
  additive, a minor version.

## Decisions

1. **One vocabulary.** Every workflow reports through `run-event/2` with the kinds the runtime already accepts.
   Nothing is added to `orchestrate-work`'s journal kinds. A standalone workflow writes no delivery record.
2. **The kernel owns the run's lifecycle.** `run.started`, `decision.recorded`, `blocker.recorded` and `run.ended`
   move from `execute-work`'s `EMITTED_KINDS` entry to `$compound-delivery-kernel`, which every delivery workflow
   already applies. The kinds stay owned once. `execute-work` keeps `ticket.read`, `posture.declared`,
   `gate.reported`, `pr.opened` and `review.round.closed`, and names the moved kinds by reference. The rule the kernel
   states: a workflow the user invoked starts the run, and a workflow another workflow invoked joins the current run
   and starts none. A `deliver-work` request is one run from routing to its last stage. Only the workflow that
   started the run ends it.
3. **Phases name the workflow.** At each workflow's entry and exit, the kernel has it emit `activity.observed` with
   `phase` set to the workflow's stage name: `route`, `plan`, `diagnose`, `execute`, `review` or `compound`. The view
   reads the phase from these and nothing else. `run.started`'s `workflow` stays the release identity it already is.
   `wait.started` and `wait.resolved` mark every stop for an approval or a decision, with `waitingOn: human` where
   the operator is needed.
4. **Same capability rule.** Emission stays optional, declared by the root instruction file, and silent when absent,
   with no handoff and no blocker. A refused or failed emission changes no outcome. What is emitted is observability,
   not evidence, and nothing gates on it.
5. **The live server watches the run store.** When the binding's views declare `runs`, `live` reads `runsDir` once
   from `runs list --json`. It then adds the store to the watch key by size and modification time of its directories
   and files, opened as opaque bytes and never parsed. A changed key re-renders the views and pushes to open pages,
   as a record change does today. The store's layout is the runtime's, so a `runsDir` that can't be read leaves the
   record's own watch working and drops only the runs watch, which the page says.
6. **No record is not an error.** `live` starts and serves when the state directory holds no record. With no record,
   it renders each live view with an empty document on stdin, and the binding's runs renderer is its front page. A
   view that needs a record says there is no delivery under way. Once a record appears, `liveHome` takes the front
   page back.
7. **A runs page in the line map's language.** `render.py runs` is a new live renderer, `runs.html`, built from the
   line map's typefaces, colour tokens and run summary. "Now" lists open runs, each with its line of phases, its
   current activity and next step, and an amber "Needs you" for a wait on a human. Each run opens a detail panel with
   its gates (label, outcome, duration), decisions, blockers, change request, review rounds through the existing run
   summary track and cards, and result with cost. Ended runs follow, newest first, and link to the history. A run
   bound to a delivery's piece names that piece and links to it on the line map. The page reads runs only through the
   existing `runs` commands, under the existing deadline, scrub list and 4 MiB budget.
8. **One server per repository, started once.** `live --if-absent` prints the address of the live server already
   serving this state directory and exits 0. Otherwise it serves as `live` does. The server records its address and
   PID in the state directory, and `--if-absent` checks that the PID is alive and answers as this server
   (`docs/solutions/test-failures/a-ready-wait-that-accepts-any-listener-checks-another-runs-server-2026-11-09.md`)
   before trusting it. The kernel says: where the repository declares a live view command, a workflow that starts a
   run runs it with `--if-absent` in the background and gives the operator the address. One that joins a run does
   neither.
9. **Characterize the runtime first.** Before any prose changes, KEY-1 records, against the pinned 0.8.6 runtime in
   a fixture repository, whether each emission Decisions 2 and 3 plan is accepted for a run with no ticket and no
   candidate: `run.started` without `ticket`, `activity.observed` and `wait.started` with no candidate tree, and
   `emit` with no managed installation registered. Each refusal is put to the operator as a decision before the
   story goes on. A runtime change belongs to the harness repository and is out of this delivery's scope.

## Out of scope

- Any change to the runtime or its `run-event/2` grammar. That includes a change log the runtime might append for
  the live server.
- New journal kinds, and writing a delivery record for a standalone run.
- The hosted page and its snapshot. Runs stay on the local live server.
- Showing a lane's current activity and waits in an epic piece's detail panel. The piece's run re-renders live, and
  the runs page shows the rest. Drawing it in the panel is a follow-up.
- Changes to the history page beyond a link to and from the runs page.
- Ticket executors (`execute-linear-ticket`, `execute-jira-ticket`) beyond what they inherit by running
  `execute-work`.
- Windows, and any host other than Codex and Claude Code.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator approves creating the four tracker items, whose keys replace `KEY-1` to `KEY-4`.
- Any runtime refusal KEY-1 finds goes to the operator before KEY-1 continues (Decision 9).
- The operator grants the lanes `push` and `draftPullRequests`.
- The operator calls the release candidate KEY-4 adopts, and the final.

## Execution posture and sensors

Test-first, with KEY-1 characterization-first for the runtime's acceptance. Sensors:

- `tests/test_run_event_emission_corpus.py`, for ownership and the capability phrases.
- `tests/test_linear_qualification.py`, for corpus pins.
- `tests/test_orchestration_render.py`, for `live` with no record, the runs watch and `--if-absent`.
- `tests/test_delivery_views_render.py` and `tests/test_delivery_views_history.py`, for the runs renderer's payload.
- `tests/delivery_views_page/`, for the page, with a new golden case under `tests/fixtures/delivery-views/runs/`.
- `tests/test_own_deliveries.py`, for each new test module's entry.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.

KEY-1's characterization runs the installed runtime under Node 22.6 or newer in a temporary git repository, never
in the checkout. Live server tests bind `--port 0` and keep to their own folders. A class that waits on a real
server process is marked `run_quietly`. The new tests keep the suite inside `tests/suite-budget.json` by reusing the
existing live-server and fixture-repository builders. No number in the budget moves.

## Scenarios

KEY-1, in a fixture repository with the pinned runtime:

- Each planned emission's acceptance is recorded as a vector in `tests/fixtures/run-events/acceptance.json`. The
  test re-runs each vector against the stored runtime, so a later runtime that refuses one fails it.
- A user-invoked `plan-work` run, then a nested `execute-work`, produce one run, with phases `plan` then `execute`
  and exactly one `run.started` and one `run.ended`.

In `tests/test_run_event_emission_corpus.py`:

- `EMITTED_KINDS` lists the four lifecycle kinds under `skills/compound-delivery-kernel/SKILL.md`, and `execute-work`
  names them only by reference. A second owner of any kind fails.
- `plan-work`, `diagnose-work` and the kernel each carry the capability phrases. The kernel carries the start, join
  and end rule and the phase names.

In `tests/test_orchestration_render.py`:

- `live` over an empty state directory serves, its front page is the runs renderer's output, and the built-in page
  says there is no delivery under way. When a record appears, the front page follows `liveHome`.
- With `runs` declared, appending a file under a fixture `runsDir` re-renders and pushes within two poll intervals,
  with no record write. Without `runs`, the store is never read.
- A `runsDir` that disappears leaves record changes pushing and the page saying runs aren't being watched.
- `live --if-absent` with a live server for this directory prints its address and exits 0. With a stale address
  file, a dead PID, or a listener that answers as another server, it serves itself.

In `tests/test_delivery_views_render.py`:

- From a fixture inventory of three runs (one open and waiting on a human, one open under `execute`, one ended
  `partial`), the runs payload carries each run's phases in order, its current activity, its wait, its gates with
  durations, decisions, blockers, change request, rounds, and result with cost unit and coverage.
- A run whose `view` fails or passes the deadline shows unavailable, and the others render.
- A run bound to a record's piece carries that piece's key.

Page rows, in a new `tests/delivery_views_page/runs.test.mjs`:

- "Now" lists open runs before ended ones. A human wait reads "Needs you" with its reason and next action.
- A run's line draws its phases in order, the current one ringed.
- The detail panel draws gates, decisions, blockers, the change request and the run summary's rounds.
- A live push with a new `gate.reported` updates the open panel in place.
- A new `runs` golden case pins the page whole. Every existing line-map and history golden renders unchanged.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | KEY-1 | `skills/compound-delivery-kernel/`, `skills/execute-work/`, `skills/plan-work/SKILL.md`, `skills/diagnose-work/SKILL.md`, `skills/deliver-work/SKILL.md`, `tests/test_run_event_emission_corpus.py`, `tests/fixtures/run-events/`, `tests/test_run_event_acceptance.py` |
| 1 | 2 | KEY-2 | `skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/SKILL.md`, `tests/test_orchestration_render.py` |
| 2 | 2 | KEY-3 | `skills/delivery-views/`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |
| 3 | 1 | KEY-4 | `AGENTS.md`, `docs/own-deliveries.md`, `.agents/delivery-views.json`, `.agents/orchestration.json` |

Each lane updates the catalog entry, every selected profile and the provenance digests in the same change as its
skill edits, as the repository requires. KEY-4 runs once a release candidate holding KEY-1 to KEY-3 is applied here.

## Stories

### KEY-1. Every delivery workflow reports its run in one vocabulary

Lane 1, tier 1, not blocked. Characterization-first for the runtime's acceptance, then test-first. Files:
`skills/compound-delivery-kernel/SKILL.md`, `skills/execute-work/SKILL.md`,
`skills/execute-work/references/run-observation-contract.md`, `skills/plan-work/SKILL.md`,
`skills/diagnose-work/SKILL.md`, `skills/deliver-work/SKILL.md`, `tests/test_run_event_emission_corpus.py`,
`tests/test_run_event_acceptance.py` and `tests/fixtures/run-events/`.

Implement Decisions 2, 3, 4 and 9, and the kernel's half of Decision 8.

### KEY-2. The live server serves without a record and follows the run store

Lane 2, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/render_state.py`,
`skills/orchestrate-work/SKILL.md` and `tests/test_orchestration_render.py`.

Implement Decisions 5 and 6, and `live --if-absent` from Decision 8.

### KEY-3. A live runs page in the line map's language

Lane 2, tier 2, after KEY-2. Test-first. Files: `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/scripts/views_common.py`, `skills/delivery-views/assets/`, `skills/delivery-views/SKILL.md`,
`tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py`, `tests/delivery_views_page/` and
`tests/fixtures/delivery-views/`.

Implement Decision 7, and the history's link to and from the runs page.

### KEY-4. Watch this repository's own runs

Lane 1, tier 3, after KEY-1, KEY-2 and KEY-3. Sensor-only. Files: `AGENTS.md`, `docs/own-deliveries.md`,
`.agents/delivery-views.json` and `.agents/orchestration.json`.

Declare the run-event command in `AGENTS.md`, `runs` in `.agents/delivery-views.json`, and the runs renderer in the
binding. Say in `docs/own-deliveries.md` how to start the standing live server. Watch one directly invoked
`execute-work` run end to end, and record what it showed.
