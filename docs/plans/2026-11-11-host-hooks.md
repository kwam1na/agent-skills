---
reviewSlug: host-hooks
---

# Make host hooks a first-class part of the workflow

A new epic. The lead's wait has failed twice in real deliveries, each time because a rule written only in prose was never met: on
2026-11-07 (V26-3265) and on 2026-11-11 in an outside adopter's delivery on v1.6.0-rc.6 (V26-3270's comment). A skill has no way to have
the host run a check at a point in a session. The generation lifecycle exposes skills to Claude Code as symlinks under `.claude/skills/`
(`agent_skills/hosts/claude.py`) and never touches a host's settings. The bundled harness runtime does write hooks, but only into a
settings file it composes for a session it launches itself (`composeClaudeCodeSession`, `product-runtime/kernel.mjs`), never for a
session the operator starts. This plan makes a hook something a skill declares, the lifecycle installs, and a delivery gates, and ships
two hooks: `lead.may-stop` and `delivery.orient`.

This plan is the separately approved unit AGENTS.md requires before the release surface gains a host integration. It adds no host: it
adds one exposure kind, Claude Code hooks, to a host the lifecycle already exposes.

The items are V26-3270, moved here from V26-3306 and narrowed by Decision 9, and four new items, written below as HOOK-1 to HOOK-4 until
the tracker items exist. Each story's criteria are written in full in its section, so its tracker item can be created from it word for
word.

## Finish line

- Each item is merged to `main` through the merge queue, or closed with a recorded reason.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked on the epic.
- The full local suite passes on macOS with Python 3.11 at the commit the release candidate is tagged on.
- A minor release candidate carrying hooks is tagged, published and applied here. `.claude/settings.json` then holds exactly the two
  entries Decision 4 names, and `.agents/run-delivery.json` sets `lead.may-stop` to `enforce`.
- The live check in HOOK-4 is recorded on the epic: in a Claude Code session in this repository, a lead with a working lane and no
  listener is held once, with the `lead-wait` command named, and stops freely once `lead-wait` runs; a lane session and a session with no
  delivery stop freely; and a resumed session receives the orientation text.
- No finding is deferred: every finding any review in this delivery raises, at any severity, is fixed in the story that found it, and no
  follow-up item is filed for it.

## Out of scope

- Codex hooks. Codex documents its own hooks (`.codex/hooks.json`, trusted through `/hooks`), so a Codex adapter is possible, but it is a
  later unit. V26-3270's "Codex has no equivalent hook" is corrected in the guide (Decision 9), and no Codex file changes.
- Any event other than Claude Code's `Stop` and `SessionStart`: no `PreToolUse` guards, no `PreCompact`, no `SubagentStop`, and no lane
  stop hook. The manifest's event list holds only `stop` and `session-start` (Decision 2), so adding an event is a deliberate change.
- Hooks for a single-skill adoption receipt (`adoption` present). Such a generation installs no hooks.
- A release-gating host qualifier. The host's behaviour is proven by the recorded live check in HOOK-4, not by a qualification record.
- `.claude/settings.local.json` and the user's own `~/.claude/settings.json`. The lifecycle writes only the project file (Decision 4).
- Windows, which is not validated (AGENTS.md).
- A final (non-candidate) release. The candidate is promoted at a later batch boundary.

## Decisions

1. **One contract, three owners.** A skill declares its hooks in a manifest (HOOK-1). The lifecycle installs, records and removes one
   dispatcher entry per host event (HOOK-2). A delivery's own settings switch each hook `off`, `warn` or `enforce` (HOOK-3). Decision
   logic lives in the skill's own script and is tested without a host. Only the dispatcher knows Claude Code's input and output shapes.
2. **The manifest is `skills/<id>/hooks.json`, schema `skill-hooks/1`.** It is a provenance output of its skill, so it is catalogued,
   profiled, digested and archived like any other skill file. A skill without the file declares no hooks. Its document is
   `{"schemaVersion": "skill-hooks/1", "hooks": [...]}`, and each hook is:
   - `id`: `<skill-id>.<name>` in lowercase words joined by `-`, unique across the profile.
   - `event`: `stop` or `session-start`, and nothing else.
   - `hosts`: a non-empty subset of the skill's catalog `hosts`. In this unit the dispatcher serves only `claude-code`, so a hook
     declaring only `codex` is valid and never installed.
   - `argv`: a non-empty list whose first item is a script path relative to the skill's folder that exists among the skill's outputs.
     The dispatcher runs it with the interpreter running the dispatcher. No shell, and no path outside the skill.
   `schemas/skill-hooks.schema.json` defines it. `agent_skills/validate.py` refuses a manifest that fails the schema, a duplicate id
   across the profile, an unknown event or host, and an `argv` script outside the skill or not among its outputs, each with its own
   finding code.
3. **The dispatcher is `agent-skills hook <host> <event>`.** It reads the host's payload on stdin, and takes the project root from
   `CLAUDE_PROJECT_DIR`, or the git top level of the payload's `cwd` when that is unset. It finds the hooks the active generation's
   profile declares for that event and host, and runs each one with the payload on stdin and the project root as its working directory.
   - **Order.** Hooks run one after another, in the order `resolve_profile` returns the skills (sorted ids), then in manifest order.
   - **Budget.** The dispatcher has 25 seconds in all, under the entry's 30-second timeout, and each hook gets at most 20 of the seconds
     left. A hook that would start with fewer than 2 seconds left is skipped and counted as `allow`.
   - **The neutral verdict.** Each hook prints one JSON object: `{"verdict": "allow"}`, `{"verdict": "warn", "reason": R}`, or
     `{"verdict": "block", "reason": R}`, and for `session-start` optionally `"context": C`.
   - **Combining.** Any `block` blocks, with the reasons joined in profile order. A `warn` alone allows, and its reasons are shown to the
     user. The `context` strings are joined in profile order and cut at 9,000 characters, under Claude Code's 10,000.
   - **Claude Code's output.** A block prints `{"decision": "block", "reason": ...}` and exits 0. A warn prints `{"systemMessage": ...}`.
     Context prints `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ...}}`.
   - **Fail open.** A hook that exits non-zero, times out or prints anything else counts as `allow`. The dispatcher writes one line naming
     the hook to stderr, and never exits 2. A dispatcher that cannot start at all (an old `python3`, a missing generation) exits non-zero,
     which Claude Code treats as a non-blocking error, so the stop proceeds.
   - **One hold per stop.** When the `stop` payload has `stop_hook_active: true`, the dispatcher turns every `block` into a `warn`. A stop
     is therefore held at most once before the session stops, with no counter kept anywhere. This sits under Claude Code's own cap of 8
     consecutive continuations.
   - **No delivery, no cost.** With no hook for the event, the dispatcher prints nothing and exits 0 without starting a process.
4. **The lifecycle writes `.claude/settings.json`, never another file.** That is the project file that reaches every clone and lane
   worktree, as `.claude/skills/` already does here. `settings.local.json` exists only in the checkout that wrote it, so a lane worktree
   would never get the hook.
   - **The entries.** One entry for each Claude event that some installed hook declares, and none for an event no hook declares:
     `{"hooks": [{"type": "command", "command": C, "timeout": 30}]}` under `hooks.Stop` or `hooks.SessionStart`, with no `matcher`, where
     `C` is exactly `python3 -B "${CLAUDE_PROJECT_DIR}/.agent-skills/current/__main__.py" hook claude-code stop` (or `session-start`).
     The `-B` keeps bytecode out of the generation, which would otherwise block the next apply with `local-bytecode-divergence`. The
     command names `current`, so an update between two hook-bearing generations changes the file only when the set of events changes.
   - **Ownership.** An entry is the lifecycle's when its `command` is exactly `C`. The lifecycle never reads, moves or rewrites any other
     key or entry.
   - **Refusals.** Preflight refuses with `exposure-conflict` when the file exists but is not a JSON object, when `hooks` or an event's
     list has the wrong type, or when an entry carrying `C` differs from the expected entry. Each refusal names the fix.
   - **Writing.** The file is written as two-space-indented UTF-8 JSON with a final newline, through a temporary file renamed onto it,
     under the repository lock. Immediately before the rename, the file's digest is compared with the one read at preflight, and a
     mismatch refuses with `exposure-conflict` and writes nothing. If `.claude/` was absent, it is recorded in `createdDirectories`.
   - **The receipt.** It gains an optional member `hooks: {"file": ".claude/settings.json", "events": [...], "created": bool}`, where
     `created` is true when the lifecycle created the file. Removal deletes the lifecycle's entries, then the file only if `created` and
     nothing else is left in it.
   - **Recovery.** Every operation reconciles the file to the receipt it commits: install, update, remove, rollback and recover alike.
     The journal gains a phase, `exposing-claude-code-hooks`, after `exposing-claude-code`, so a fault in any phase recovers to one
     endpoint's entries, never a mixture.
   - **What the lifecycle reports.** `plan` and `diff` gain `hooks: {"added": [...], "removed": [...]}`, by event. `status` reports a
     blocker, `local-divergence: hooks`, when the file's entries no longer match the receipt.
   - **Upgrades and downgrades.** An apply runs the target archive's own code (`python3 -B <archive> --product apply`), so the first
     hook-bearing generation installs its entries in its own apply. A receipt with `hooks` fails an older generation's
     `validate_receipt`, whose top-level object takes no unknown member. So applying an older archive over it is refused, and the way back
     is `rollback`, run by the newer code, which restores the prior receipt and removes the entries. The release notes say so.
5. **Claude Code holds hooks back until the folder is trusted.** A session in an untrusted folder runs none, and nothing in this unit
   works around that. The guide says a lead works in a trusted folder, and `doctor` cannot see trust, so it says nothing about it. Under
   `-p` or the SDK the folder counts as trusted, so the hooks run in unattended sessions too.
6. **A delivery switches each hook in `.agents/run-delivery.json`.** The file gains `hooks: {"lead.may-stop": M, "delivery.orient": M}`,
   where `M` is `off`, `warn` or `enforce`. A missing key means `warn` for `lead.may-stop` and `enforce` for `delivery.orient`, which
   only ever allows. `warn` returns `warn` where `enforce` would return `block`. `references/config.md` documents the field, and `doctor`
   refuses an unknown hook id or mode. This repository sets `enforce` for `lead.may-stop` in HOOK-4.
7. **Roles come from the payload's `cwd`, never from the session.** Claude Code's payload carries `cwd` and `session_id`, but no lead
   command can learn its own session id. The hook lists the repository's checkouts with `git worktree list --porcelain`, and reads the
   open deliveries in each checkout's state directory, resolved as run-delivery resolves it today. So:
   - A session whose `cwd` is inside a lane checkout an open delivery records is that lane. `lead.may-stop` allows it.
   - A session whose `cwd` is inside the checkout holding an open delivery's state is a lead of that delivery.
   - When one open delivery in that checkout needs a listener and has none, `lead.may-stop` blocks, naming the delivery and its exact
     `lead-wait` command.
   - When two or more open deliveries share the checkout, it can't tell which lead this session is, so it returns `warn`, never `block`,
     naming each delivery that has no listener. The guide says to lead each delivery from its own checkout, as `lead-plan` already
     allows.
   - Any other session, and any closed delivery, is allowed with no output.
8. **What `delivery.orient` may say.** It prints only structured fields:
   - for a lead: the delivery key, its stage, the count of Needs you items, and the exact `lead-wait` command when a listener is needed;
   - for a lane: the delivery key, the story key, the lane's brief path, and the next lane command.
   It never prints free text from the journal, a hand-off or a tracker item, so no lane-written or tracker-written text reaches a lead's
   context through a hook. The text is cut at 4,000 characters. It runs on every `SessionStart` source, so a compacted or resumed
   session is told again.
9. **V26-3270 moves here, narrowed to its Scopes 1 and 2 and its AC1, AC2 and AC4.** Its Scope 3 and AC3, the Stop hook, become HOOK-3,
   built on the manifest and the dispatcher rather than a one-off entry "in the Claude projection".
   - **The lease** is `<state>/lead-wait.json`: `{"pid", "startedAt", "updatedAt", "after", "checkout"}`, rewritten through a temporary
     file every 60 seconds while `lead-wait` runs.
   - **A live lease** is one whose pid is alive and whose `updatedAt` is under 180 seconds old. `lead.may-stop`, the supervisor and the
     live view all read it through one function in `run_delivery.py`.
   - **The guide** names `lead-wait`, the hook and its settings, says Codex's own hooks are not installed by this generation, and keeps
     `delivery_state.py wait` as the command `lead-wait` wraps.
   - **A hold is journaled.** Each `block` or `warn` from `lead.may-stop` appends one `hook.held` entry to that delivery's journal
     through the state tool. The entry names the hook, the verdict and the missing listener, with `lane` null. By Decision 3 there is at
     most one per stop. `hook.held` is not in `WAIT_KINDS`, so it never wakes a `wait`. The record's history shows it as any other entry.
     An `allow` writes nothing.
10. **The release is a minor candidate, applied here in the change that turns enforcement on.** The receipt and the archive's file set
    change, so the candidate is applied here before promotion, as AGENTS.md requires of a record-shape change. Once HOOK-1, HOOK-2,
    V26-3270 and HOOK-3 have merged, the lead runs the full suite on macOS with Python 3.11 and tags the next minor candidate on a commit CI
    passed as the tip of a push to `main`. HOOK-4 then applies it on its branch, which writes the two entries into `.claude/settings.json`
    and commits them with the generation, and sets `lead.may-stop` to `enforce`. This delivery's own lead runs on the old generation until
    HOOK-4 merges, then restarts its supervisor with `lead-run --resume`.

## Units and order

Two lanes. Lane 1 holds the lifecycle and release tooling, and lane 2 the delivery skills. HOOK-3 needs HOOK-1's manifest schema and
V26-3270's lease, so it goes last on lane 2, after HOOK-1 merges. HOOK-4 needs every other item and the tagged candidate.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | HOOK-1, HOOK-2 | `agent_skills/`, `schemas/`, `tests/test_hooks.py`, `tests/test_lifecycle.py`, `tests/lifecycle_support.py`, `tests/test_host_exposure.py`, `tests/test_receipt.py`, `tests/test_registry.py`, `tests/test_release_archive.py`, `tests/test_release_entry_budget.py`, `tests/test_release_integration.py`, `docs/product-generation.md` |
| 1 | 2 | V26-3270, HOOK-3 | `skills/run-delivery/`, `skills/orchestrate-work/`, `skills/delivery-views/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_lead_wait.py`, `tests/test_run_delivery_hooks.py`, `tests/test_orchestration_render.py`, `tests/test_orchestration_state.py`, `tests/delivery_views_page/`, `catalog.json`, `profiles/`, `docs/solutions/` |
| 2 | 1 | HOOK-4 | `.agent-skills/`, `.agents/skills/`, `.claude/skills/`, `.claude/settings.json`, `.agents/run-delivery.json`, `docs/own-deliveries.md`, `tests/test_own_deliveries.py` |

**Shared files.** `provenance.lock.json` is regenerated by the writer at sync and never merged by hand. `catalog.json` and `profiles/` are
lane 2's. HOOK-1 changes neither, because its rows build fixture corpora. No test writes `.claude/settings.json` in the repository root;
every lifecycle row works in its own temporary repository. The hook rows run scripts in temporary folders with their own state
directories and never start a Claude Code session. No shared ports, browsers or databases.

**Pins that will go stale.** Each item re-derives the pins its change makes stale and names them in its hand-off:
- HOOK-1: the archive's `agent_skills/` file list in `release.py` and the entry budget in `tests/test_release_entry_budget.py`.
- HOOK-2: `JOURNAL_PHASES`, the receipt schema's members, and the `agent-skills-plan/1` and `agent-skills-diff/1` shapes, which gain one
  member each.
- HOOK-3: the journal-kind enum in `skills/orchestrate-work/references/schemas.json`, and the provenance outputs, which gain
  `skills/run-delivery/hooks.json`. That entry is added to `provenance.lock.json` by hand, as adding an output always is, and then
  digested by the writer.
- V26-3270: the lead commands' printed output.

## Stories

### HOOK-1. Let a skill declare host hooks, and run them through one dispatcher

Lane 1, tier 1, not blocked. Test-first, per Decisions 1, 2 and 3. Files: `agent_skills/hooks.py`, `agent_skills/cli.py`,
`agent_skills/validate.py`, `agent_skills/release.py`, `schemas/skill-hooks.schema.json`, `tests/test_hooks.py`, `tests/test_registry.py`,
`tests/test_release_archive.py` and `tests/test_release_entry_budget.py`.

Acceptance criteria:
1. A manifest matching Decision 2 validates. Each refusal Decision 2 lists has its own row and finding code: a schema failure, a
   duplicate id across the profile, an unknown event, a host outside the skill's `hosts`, and an `argv` script outside the skill or not
   among its outputs.
2. `agent-skills hook claude-code stop`, run against a fixture generation, produces Claude Code's output for each combination:
   - allow, warn and block from a single hook;
   - block plus warn, with the reasons joined in profile order;
   - a non-zero exit, a timeout and invalid JSON, each counted as allow with one stderr line naming the hook;
   - `stop_hook_active: true` turning a block into a warn;
   - no hook for the event, which prints nothing, exits 0 and starts no process.
3. `agent-skills hook claude-code session-start` joins `context` strings in profile order, cuts them at 9,000 characters, and prints
   `hookSpecificOutput`.
4. The dispatcher never exits 2. That is pinned by a row where every hook fails.
5. The release archive carries `agent_skills/hooks.py` and `schemas/skill-hooks.schema.json`, within the entry budget.

Sensors: `tests/test_hooks.py`, `tests/test_registry.py`, `tests/test_release_archive.py`, `tests/test_release_entry_budget.py`,
`scripts/validate-registry.py`.

### HOOK-2. Install, record and remove Claude Code hook entries with the generation

Lane 1, tier 1, after HOOK-1. Test-first for the new behaviour, characterization-first for the `plan`, `diff` and receipt shapes it
extends. Per Decision 4. Files: `agent_skills/hosts/`, `agent_skills/lifecycle.py`, `agent_skills/journal.py`,
`agent_skills/receipt.py`, `agent_skills/release.py`, `schemas/receipt.schema.json`, `tests/test_release_entry_budget.py`, `tests/test_lifecycle.py`, `tests/lifecycle_support.py`,
`tests/test_host_exposure.py`, `tests/test_receipt.py`, `tests/test_release_integration.py` and `docs/product-generation.md`.

Acceptance criteria:
1. Installing a generation whose profile declares a `stop` hook writes exactly the `Stop` entry of Decision 4. It records `hooks` in the
   receipt, records `.claude/` in `createdDirectories` when it created it, and leaves every other key and entry byte-equal after parsing.
2. Updating to a generation that adds `session-start` adds only that entry. Updating to one with no hooks removes both, and deletes the
   file when `created` is true and nothing else is left. Removing a generation works the same way. A rollback restores the prior
   receipt's entries.
3. A fault injected at every journal phase, `exposing-claude-code-hooks` included, recovers to one endpoint's entries.
4. Preflight refuses each case Decision 4 lists, naming the fix: a non-object file, a wrong-typed `hooks`, and an altered owned entry. A
   change to the file between preflight and the rename refuses with `exposure-conflict` and writes nothing.
5. `plan` and `diff` report `hooks.added` and `hooks.removed`. Their other members are unchanged, which the characterization rows pin.
6. `status` reports `local-divergence: hooks` when an owned entry is removed or edited by hand.
7. An adoption receipt installs no hooks.
8. `docs/product-generation.md` says what an apply writes to `.claude/settings.json`, how to remove it, and that downgrading goes
   through `rollback`.

Sensors: `tests/test_lifecycle.py`, `tests/test_host_exposure.py`, `tests/test_receipt.py`, `tests/test_release_integration.py`,
`scripts/validate-registry.py`.

### V26-3270. Make the lead's wait impossible to miss: printed and visible when absent

Lane 2, tier 1, not blocked. Test-first, per the item's Scopes 1 and 2 and its AC1, AC2 and AC4, and Decision 9. The item's title and
Scope 3 are updated on the tracker to match. Rows:
- `lead-init`, `lead-assign`, `lead-accept` and `lead-run --resume` print the background `lead-wait` command with the right `--after`
  when a lane works or a candidate is queued, and print nothing about it otherwise.
- `lead-wait` writes and refreshes the lease of Decision 9 and removes it when it ends.
- A lease whose pid has died, or whose `updatedAt` is 180 seconds old, counts as absent.
- With a lane working and no live lease, the supervisor's Needs you and the live view both name the missing listener, and a running
  `lead-wait` clears both.
- The guide's "The lead's wake", run-delivery's `SKILL.md` Lead row and orchestrate-work's Lead authority sentence name `lead-wait`, with
  presence pins.

Files: `skills/run-delivery/`, `skills/orchestrate-work/`, `skills/delivery-views/`, `tests/test_run_delivery.py`,
`tests/test_run_delivery_supervisor.py`, `tests/test_lead_wait.py`, `tests/test_orchestration_render.py`, `tests/delivery_views_page/`,
`catalog.json` and `profiles/`. Sensors: those test modules, the page lane, `scripts/validate-registry.py`.

### HOOK-3. Hold a lead's stop while nobody listens, and orient every session in a delivery

Lane 2, tier 1, after V26-3270 and HOOK-1. Test-first, per Decisions 6, 7 and 8. `skills/run-delivery/hooks.json` declares
`run-delivery.lead-may-stop` (`stop`) and `run-delivery.delivery-orient` (`session-start`), both for `claude-code`. Their scripts are
`run_delivery.py hook lead-may-stop` and `run_delivery.py hook delivery-orient`, which read the payload and print the neutral verdict.
Their ids in `.agents/run-delivery.json` are `lead.may-stop` and `delivery.orient`. Files: `skills/run-delivery/`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`tests/test_run_delivery_hooks.py`, `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`, `catalog.json`, `profiles/` and
`provenance.lock.json`.

Acceptance criteria:
1. `lead-may-stop` returns `block` with the delivery key and its exact `lead-wait` command for a lead checkout with one open delivery,
   a lane working or a candidate queued, no live lease, and mode `enforce`.
2. It returns `warn` with the same reason under mode `warn` (and with the key missing), and returns nothing under `off`.
3. It returns `allow` in each of these cases, one row each:
   - a live lease;
   - a lane checkout, including a lane worktree found through `git worktree list`;
   - a folder with no delivery;
   - a closed delivery;
   - nothing waiting on the lead.
4. A dead pid's lease and a lease 180 seconds old are each treated as absent and return `block`.
5. Each `block` and `warn` appends exactly one `hook.held` entry to the delivery's journal, as Decision 9 says. An `allow` appends nothing,
   and a running `wait` does not end on `hook.held`.
6. Two open deliveries in one checkout return `warn`, naming each that lacks a listener, under `enforce` too.
7. `delivery-orient` prints Decision 8's fields for a lead and for a lane, and nothing for any other session. A row plants hostile text
   in a journal summary, a hand-off and a tracker title, and none of it appears in the output.
8. `doctor` refuses an unknown hook id or mode in `.agents/run-delivery.json`, naming the fix. It reports, without refusing, when the
   installed generation's `.claude/settings.json` lacks the entries the receipt records, and when `python3` on `PATH` is below 3.11.
9. Run through the shipped dispatcher against a fixture generation, the hook's block reaches Claude Code's output as
   `{"decision": "block", ...}`, and the second stop, with `stop_hook_active: true`, comes out as a warn.
10. `references/config.md` documents `hooks` in `.agents/run-delivery.json`, and the guide documents what each hook does and when it
   holds a stop.

Sensors: `tests/test_run_delivery_hooks.py`, `tests/test_run_delivery.py`, `tests/test_hooks.py`, `scripts/validate-registry.py`.

### HOOK-4. Apply the hooks candidate here and enforce the lead's wait

Lane 1, tier 2, after HOOK-1, HOOK-2, V26-3270 and HOOK-3, once the lead has tagged and published the candidate (Decision 10).
Test-first: `StandingBinding` rows in `tests/test_own_deliveries.py` pin `lead.may-stop: enforce` in `.agents/run-delivery.json`, and pin
exactly Decision 4's two entries in the committed `.claude/settings.json`. Then the lane applies the candidate as
`docs/own-deliveries.md` says, which writes those entries, and commits the generation, the settings file and the binding change together.
`docs/own-deliveries.md` gains a "Hooks" section saying what runs, how to turn it off for one delivery, and that the folder must be
trusted. Before it hands off, the lane records the live check of the finish line on the epic: a `claude -p` session in a scratch clone,
plus one interactive resume. Files: `.agent-skills/`, `.agents/skills/`, `.claude/skills/`, `.claude/settings.json`,
`.agents/run-delivery.json`, `docs/own-deliveries.md` and `tests/test_own_deliveries.py`. Sensors: `tests/test_own_deliveries.py`,
`scripts/validate-registry.py`, `run_delivery.py doctor`, and the recorded live check.

## Execution posture

`test-first` for HOOK-1, V26-3270, HOOK-3 and HOOK-4. HOOK-2 is `test-first` for the hook exposure, and characterization-first for
the `plan`, `diff` and receipt shapes it extends, pinned before they change. Lanes work to the focused lane profile, with no full suite
in a lane. The queue runs the selected checks on the synced head, `fullHealth` runs after each merge, and the lead runs the full suite
before tagging.

## Approval handoffs

- The plan itself, before `lead-setup`. Approving it is the separately approved unit AGENTS.md requires.
- Creating the epic and HOOK-1 to HOOK-4 in Linear from this plan's sections, moving V26-3270 here from V26-3306, and narrowing its title
  and Scope 3 as Decision 9 says. The lead then replaces the HOOK keys in this plan with the tracker keys before `lead-setup`.
- The minor release candidate's tag, and its apply here in HOOK-4.
- Turning `lead.may-stop` to `enforce` in this repository (HOOK-4).
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Plan review

Every lens raises every finding it can state against this plan in the round it reads it, and ends with an approval condition for each,
as `plan-work` requires. The decisions above settle the questions a lens would otherwise raise:
- which settings file is written, and why (Decision 4);
- crash recovery and concurrent edits (Decision 4);
- downgrade (Decision 4);
- trust (Decision 5);
- unknown session identity (Decision 7);
- text injected into a session (Decision 8);
- runaway holds and the time budget (Decision 3);
- how a hold is recorded (Decision 9);
- a hook that crashes or a `python3` that is too old (Decisions 3 and 4);
- bytecode in the generation (Decision 4);
- the relation to V26-3270 (Decision 9).

A finding against one of them should name the decision it disputes.
