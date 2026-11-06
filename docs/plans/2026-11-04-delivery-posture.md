---
reviewSlug: delivery-posture
---

# Let one declaration on the base set a repository's delivery posture

Two tickets, V26-3124 and V26-3125, under the epic V26-3126. The tickets are binding for what each one does. This plan settles the design questions they
leave open, so both can start against one contract, and decides how the work reaches `main` across two repositories.

## Finish line

- V26-3124's source is merged to `main` of `kwam1na/agent-delivery-harness`, and its built runtime is merged to `main` of `kwam1na/agent-skills` through the
  merge queue.
- V26-3125 is merged to `main` of `kwam1na/agent-skills` through the merge queue.
- CI's Ubuntu job and `cut-release` pass on the last merged commit of `kwam1na/agent-skills`, with the run linked.

## Out of scope

- Cutting a release, which waits for the operator's word. The tickets' release scope and Athena's removal of its local version follow as one tracked item, filed
  at close-out.
- Which checks a repository runs under `focused`, and a per-delivery waiver.
- `admit`, `check` and `prepare`, which keep their behaviour under either posture.
- The hosted action in the harness's `packages/action`, which calls the record check directly. The tickets name four commands, and the action stays as it is.
- The two commits on the harness's `main` after `fa63884`, which belong to V26-2347, still in progress.

## Decisions

### The declaration

One file, `.agents/delivery-posture.json`, read only from the tip of the protected base. Both the harness and `run-delivery` read this file and nothing else, so
one declaration governs and a repository whose `run-delivery` sets `"harness": "ignore"` still reaches it. It isn't a `HarnessConfig` member, because config loads
from the candidate's tree, which a branch controls, and reading it would run validation planning.

```json
{
  "schemaVersion": "delivery-posture/1",
  "posture": "focused",
  "reason": "The gate is still being optimized.",
  "setBy": "owner",
  "since": "2026-11-04"
}
```

`schemaVersion`, `posture` (`gate` or `focused`) and a non-blank `reason` are required, for either posture. Any other member is ignored. Athena's
`athena-delivery-posture/1` is not accepted: an adopter changes the file's `schemaVersion` in its adoption commit.

### How it resolves

Given a base, a reader runs a best-effort fetch with `--no-tags`, then `git ls-tree` and `git show` against a full ref, so a tag or local branch of the same short
name can't win. Every network call, the fetch and the harness's `ls-remote` alike, is bounded at 15 seconds. Offline, the last fetched tip decides. It reads the tip, never a merge base. Every reader gives the same answer for the same bytes:

| What the base holds | Posture | Code |
| --- | --- | --- |
| A valid `focused` declaration | `focused` | none |
| A valid `gate` declaration | `gate` | none |
| No file at the tip | `gate` | `posture_undeclared` |
| Not valid UTF-8 JSON, an object failing the shape above, or an unknown `posture` | `gate` | `posture_malformed` |
| A symlink, tree or submodule at that path, or a blob over 64 KiB | `gate` | `posture_malformed` |
| The base ref can't be resolved or read, or `git` fails or times out | `gate` | `posture_unreadable` |

The result is `{posture, reason, source, baseTipSha, code}`. `source` is the base as text, such as `origin/main`, or `fallback` where nothing was read. Each code's
`reason` names the path and the base.

One vector file, `tests/fixtures/delivery-posture-vectors.json` in this repository, holds one vector per case rather than per row: invalid UTF-8, a byte-order mark,
non-object JSON, missing and blank `reason`, a whitespace-only `reason` including characters Python and JavaScript trim differently, wrong and Athena `schemaVersion`,
unknown and non-string `posture`, valid `focused`, valid `gate`, no file, extra members, symlink, tree, submodule, exactly 64 KiB, one byte over, an unresolved ref, a failing `git` and a hanging `git`, a local branch and a tag named as the base's short name carrying `focused` with the real base at `gate` (`gate`), and a failed fetch with a working read of a last-fetched `focused` tip (`focused`, no code, source the base as text). It runs
here against both `run_delivery.py`'s reader and the built runtime's exported resolver, and they must give the same posture, code and source. That comparison is what keeps
the two readers from drifting.

### The harness's commands (V26-3124)

**Trust.** The base is `config.baseRef`, which the candidate's config supplies, so its value can't be trusted on its own. It must name `origin/<branch>`, and `<branch>`
must be the remote's default branch, read from `git ls-remote --symref origin HEAD` and, offline, from `refs/remotes/origin/HEAD`. A mismatch, or a default that can't be
determined, resolves to `gate` with `posture_unreadable`. This holds however `baseRef` was changed: in `harness.config.ts`, in a module it imports, or in an uncommitted
edit, and a branch that publishes itself as its own `baseRef` gets nothing.

**No side effect on the base.** The harness's commands don't fetch today, and moving `refs/remotes/origin/<branch>` would turn a passing `verify` into `base_tip_moved`
in a repository that declares nothing. So the harness reader fetches only into a private ref, `git fetch --no-tags --refmap= origin +refs/heads/<branch>:refs/delivery/posture/<branch>`,
and reads from it. When that fetch fails, either ref can be the stale one: an earlier run left the private ref, and a normal fetch moves only the tracking ref. So it reads
`refs/remotes/origin/<branch>`, which must be readable, and the private ref if it exists, and resolves `focused` only when both say `focused`, with the tracking ref's tip as the base tip. Otherwise it is `gate`, with the base tip and code of the ref that said so, and `posture_unreadable` when the tracking ref can't be read.

**One exported resolver.** `packages/kernel/src/index.ts` exports `resolveDeliveryPosture` and its result type, so the built runtime's `kernel.mjs` carries them. A
repository that chooses its own checks by posture calls this. That is how Athena's `scripts/pre-push-review.ts` keeps its behaviour once its module is gone, with
`FOCUSED_PUSH_CHECKS` moving into Athena's own script.

Under `focused`:

- `verify`, `gate`, `record` and `resume` each exit 0 with the one outcome `focused`, naming the posture, its reason, its source and the base tip. None of them
  reads or writes a delivery record, runs an obligation, or reports `delivery_record_missing` or an admission blocker.
- No obligation applies. Review evidence and focused checks stay the repository's, as the ticket's scope says.
- The outcome is the whole record. There is no new record shape and no new run event kind.

Under `gate`, each command's output and exit code are byte for byte what they are today for a valid `gate` declaration, `posture_undeclared` and `posture_unreadable`, so
existing fixtures and a repository whose `baseRef` is a plain `main` don't change. The result object still carries the code. Only `posture_malformed` adds its code and
reason to what a command reports, since there a repository has declared something and it didn't take.

### Run-delivery's close-out (V26-3125)

- `run_delivery.py` gets one reader, used by `full_health_step`, `needs_you` and `lead-close`. It reads the delivery's own `<remote>/<base>` fresh on each use, through
  git and never the shelf helper or the lead's working tree, and is never frozen at `lead-init` or held across a supervisor's passes. An integration base is the lead's
  own, so the default-branch rule is the harness's alone.
- Under `focused`, `full_health_step` starts no run and succeeds, and a failed run at this base head holds nothing. The skip goes in the full-health log with no
  `result` member, so `full_health_results` can never read it as a pass, and a later `gate` resolution at the same head starts a real run.
- The first skip journals one `decision.recorded`, and so does each posture change at close-out, however many passes see them. The journal entry, the supervisor's
  close-out summary, `closed.json` on both close paths, and the live view's plan chapter say the run was skipped under the posture, naming the source and the reason.
- The plan chapter's carrier is an additive optional `delivery-plan/1` member, `closeOut: {fullHealth: "skipped", posture, source, reason}`, in the schema and validator in
  `views_common.py`, and it renders as skipped. The full-health log's skip entry becomes a producer input of `plan.json`, which is rebuilt in the step that records the
  skip, before close-out's final render. `closeOut` follows the log's latest result or skip entry, so a later real run clears it and a `lead-resolve` rerun entry doesn't. No approval state is reused for it, since `done` reads like a pass.
- Nothing runs instead. There is no lighter close-out check to configure.
- `lead-close` holds nothing new. Under `focused` it records the skip the same way when no full-health result exists. Under `gate` it does what it does today.
- `orchestration-binding/1` doesn't change. `fullHealth` stays required, so restoring `gate` needs no binding change.
- `orchestrate-work`'s `SKILL.md` and guide state the same rule for a delivery without the supervisor.

### Two repositories

The runtime's source is `kwam1na/agent-delivery-harness`. `product-runtime/` is its build output, and today it matches a build of that repository's `fa63884` byte for
byte. So V26-3124 has two change requests:

1. **The source,** a pull request to the harness's `main`, bumping the kernel and CLI packages to 0.7.8. The lead merges it, once reviewed.
2. **The build,** `product-runtime/` rebuilt with `npm run product:build-runtime` from a clean checkout of a branch `runtime/0.7.8` in that repository, which is
   `fa63884` plus the merged change cherry-picked. The commit it was built from is named in the change request and in `docs/product-generation.md`. This keeps V26-2347's
   unfinished commits out of what adopters run. A later runtime build from `main` picks up both.

**Evidence on a red base.** That repository's main CI has been red since before this work and requires no check, so a bare `npm run check` proves little, and it stops at
its first failing step. The lane runs `npm run typecheck`, `npm run sensor`, `npm run sensor:cli` and `npx vitest run` as separate steps, on `main` before the work starts and on `fa63884`, and records the
failures of each by test name. Its evidence is a per-test comparison: on the pull request against `main`, and on the `runtime/0.7.8` tip, the tree that ships, against
`fa63884`, with no new failure and the new scenarios passing by name. Both runs are named in the build's change request.

The queue carries the second change request. The first is outside it because the binding serves only this repository.

### Order

The tickets record V26-3125 as blocked by V26-3124 because it settled the declaration. This plan settles it, so both start now.

`tests/test_run_delivery.py` pins the runtime version and lane 2 owns that file. That assertion compares the descriptor with a literal, so V26-3125 removes it, and V26-3124's
build adds a literal `0.7.8` pin to `tests/test_product_release.py`, which lane 1 owns. `tests/test_cut_release.py` replaces the literal `"0.7.7"` in its repeated-digest
subtest, so V26-3124 changes it to use the descriptor's own `runtimeVersion`. V26-3125 merges first, since its reader creates the vector file, and V26-3124's build is handed
off after that.

### A base that is already red

CI's Ubuntu job on `fe6ff2f` failed one test,
`SupervisorProcessTests.test_a_running_supervisor_restarts_its_children_and_reports_one_that_wont_stay_up`, with `KeyError: 'children'`. `write_lock` truncates and then
writes on every restart, and `lock_of` maps an empty read to `{}`, so a read during a restart finds no children. Earlier commits passed that job. V26-3125 owns that suite
and fixes the race: every lock read in that test retries within a bound. The finish line needs that job green. CI's macOS job has timed out in `tests.test_run_delivery`
for several commits. It informs and doesn't gate, so it stays out of this delivery.

## Stories

### V26-3124. Let a repository declare its delivery posture, focused or gate, in the harness

- Lane: lane-1. Not blocked. Its build is handed off after V26-3125 merges.
- Files in the harness repository: `packages/kernel/src/`, `packages/cli/src/commands/verify.ts`, `gate.ts`, `record.ts`, `resume.ts`, their tests, the package versions, the docs.
- Files here: `product-runtime/**`, `docs/product-generation.md`, `README.md`, `skills/bootstrap-agent-harness/**`, `tests/test_product_release.py`, `tests/test_product_descriptors.py`, `tests/test_product_launcher.py`, `tests/test_cut_release.py`, `tests/built_product.py`, `tests/test_delivery_posture_vectors.py`.
- Scenarios, written first in the harness's suites:
  - A table over `verify`, `gate`, `record` and `resume`: base `focused`, no record, exit 0 and the `focused` outcome with posture, reason, source and base tip.
  - For each of those four commands, base `focused`, a corrupt record present, an obligation whose command writes a sentinel: the record's bytes unchanged, no new file in the record or run store, no sentinel.
  - Base `focused`: `admit` and `check` refuse what they refuse under `gate`.
  - Base `gate`, candidate `focused`: still gated.
  - A local branch and a tag named `origin/main` carrying `focused`, base `gate`: `gate`.
  - A candidate branched from a `focused` base with the base later `gate`, and the mirror case: the tip's posture.
  - The remote moved and the tracking ref stale: the new tip's posture, `refs/remotes/origin/main` unmoved, and `verify`'s record outcome unchanged in a repository that declares nothing.
  - An unreachable remote with a fetched `focused` tip: `focused` with no code. A hanging fetch with the same tip: the bound holds, `focused` with no code.
  - Remote unreachable, `refs/remotes/origin/HEAD` names the default, `baseRef` a self-publishing branch carrying `focused`: `gate` with `posture_unreadable`.
  - Remote unreachable, `refs/remotes/origin/HEAD` absent, a `baseRef` that would otherwise read `focused`: `gate` with `posture_unreadable`.
  - A hanging `ls-remote` with `refs/remotes/origin/HEAD` absent: the bound holds, `gate` with `posture_unreadable`.
  - A hanging `ls-remote` with `refs/remotes/origin/HEAD` naming the default and a fetched `focused` tip: `focused` with no code, each network call within its bound.
  - Fetch failing, private ref at `focused` and tracking ref at `gate`, then the reverse: `gate` both ways, with the base tip of the ref that said `gate`.
  - Fetch failing, private ref and tracking ref at two different `focused` tips: `focused` with no code, and the base tip is the tracking ref's.
  - Fetch failing, tracking ref missing, private ref at `focused`: `gate` with `posture_unreadable`.
  - Fetch failing, private ref at `focused`, tracking ref malformed: `gate` with `posture_malformed`.
  - Fetch succeeding, a stale private ref: it moves to the remote's new tip, and the reported base tip is that tip.
  - A valid `gate` declaration and `posture_unreadable`: output byte for byte as before.
  - `baseRef` moved to a branch that publishes itself, by a direct edit, by an imported module and by an uncommitted edit: `gate` with `posture_unreadable`.
  - A malformed declaration: gated, with the named code in the output. No declaration, and a plain `baseRef` of `main`: output byte for byte as before.
- Scenarios here: a built product's `verify`, `gate`, `record` and `resume`, with `focused` on a fixture's base and no record, exit 0 and name the posture and source. The
  vector file runs against the built runtime's exported `resolveDeliveryPosture`, and the same results as `run_delivery.py`'s reader. A test imports that export from the built `kernel.mjs`.
- Sensors: the harness's `npm run typecheck` and `npx vitest run`, compared as Two repositories says; here, the listed test modules.
- Adoption notes: the file, its schema, the fallback table, and a mapping that names what replaces each Athena site in V26-3124's fifth acceptance criterion.
  Compounding: record what Athena's use since 2026-11-04 needed.

### V26-3125. Make run-delivery's supervisor honour the focused posture at close-out

- Lane: lane-2. Not blocked. Merges first.
- Files: `skills/run-delivery/**`, `skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/**`, `provenance.lock.json`, `tests/fixtures/delivery-posture-vectors.json`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_delivery_views*.py`, `tests/delivery_views_fixtures.py`, `tests/delivery_views_page/**`.
- Scenarios, written first:
  - Every one of V26-3125's six, and every vector in the vector file.
  - The lead's working tree and a local unpushed commit declare `focused`, remote base `gate`: `fullHealth` runs.
  - The delivery's base differs from the binding's base, with opposite declarations: the delivery's base decides.
  - One running supervisor across two passes, the remote base flipped between them and the tracking ref not updated by the fixture: the second pass honours the new tip.
  - A `failed` entry at exactly the tip that holds `focused`: `needs_you` holds nothing and `full_health_step` succeeds with no run. The ticket's moved-head scenario as well.
  - On both close paths, the journal entry, close-out summary, `closed.json` and plan chapter contain "skipped", the posture, the source and the reason, and no pass wording.
  - A skip at a head, then a `gate` resolution at that head: a real run starts. When it passes and the delivery closes, `plan.json` has no `closeOut` skip, the plan chapter and
    `closed.json` don't say skipped, and the close-out summary reports the pass. The same with the `gate` declaration arriving at a later head.
  - In `tests/delivery_views_page/line-map-plan.test.mjs`, a plan with `closeOut` renders "skipped", the posture, the source and the reason, with no pass wording.
  - After a skip, a `lead-resolve full-health` rerun entry leaves `closeOut` in place while the posture is `focused`.
  - A corpus pin on the new rule in `orchestrate-work`'s `SKILL.md`, and on the guide's close-out step's new qualifying wording.
  - Two or more passes write exactly one `decision.recorded` for the skip and one for a posture change.
  - The supervisor test's race, with a read during a restart retrying within its bound.
- Sensors: `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_tracker.py`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_plan.py`, `tests/test_delivery_views_plan_fixtures.py`, `tests/test_delivery_views_page.py`.
- Adoption notes: `references/config.md` and the adoption guide.
- Compounding: record what, if anything, a broad run would have caught on V26-2985 and V26-3111.
