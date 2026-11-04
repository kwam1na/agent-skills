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

A best-effort bounded fetch of the base, 15 seconds, then `git ls-tree` and `git show` against the full ref `refs/remotes/<remote>/<branch>`, so a tag or local
branch of the same short name can't win. Offline, the last fetched tip decides. Every reader gives the same answer:

| What the base holds | Posture | Code |
| --- | --- | --- |
| A valid `focused` declaration | `focused` | none |
| A valid `gate` declaration | `gate` | none |
| No file at the tip | `gate` | `posture_undeclared` |
| Not valid UTF-8 JSON, an object failing the shape above, or an unknown `posture` | `gate` | `posture_malformed` |
| A symlink, tree or submodule at that path, or a blob over 64 KiB | `gate` | `posture_malformed` |
| The base ref can't be resolved or read, or `git` fails or times out | `gate` | `posture_unreadable` |

The result is `{posture, reason, source, baseTipSha, code}`. `source` is the base as text, such as `origin/main`, or `fallback` where nothing was read. Each code's
`reason` names the path and the base. Both suites carry this table as test vectors with the same rows and codes, so the two readers can't drift apart unseen.

### The harness's commands (V26-3124)

The base is `config.baseRef`. It must name a remote-tracking branch, `origin/<branch>`; anything else resolves to `gate` with `posture_unreadable`. A candidate whose
diff against its merge base changes `harness.config.ts` resolves to `gate` with `posture_config_changed`, because that file names the base and a branch could
otherwise point `baseRef` at itself. It carries a named reason and costs a repository only the benefit, for that one candidate.

Under `focused`:

- `verify`, `gate`, `record` and `resume` each exit 0 with the one outcome `focused`, naming the posture, its reason, its source and the base tip. None of them
  reads or writes a delivery record, runs an obligation, or reports `delivery_record_missing` or an admission blocker.
- No obligation applies. Review evidence and focused checks stay the repository's, as the ticket's scope says.
- The outcome is the whole record. There is no new record shape and no new run event kind.

Under `gate`, with `posture_undeclared`, each command's output and exit code are byte for byte what they are today, so existing fixtures don't change. With any other
code, each command behaves as today and adds the code and reason to what it reports.

### Run-delivery's close-out (V26-3125)

- `run_delivery.py` gets one reader, used by `full_health_step`, `needs_you` and `lead-close`. It reads the delivery's own `<remote>/<base>` fresh on each use and
  is never frozen at `lead-init`.
- Under `focused`, `full_health_step` starts no run and succeeds, and a failed run at this base head holds nothing. The skip goes in the full-health log with no
  `result` member, so `full_health_results` can never read it as a pass.
- A posture that changes at close-out journals one `decision.recorded`, as does the first skip. The journal entry, the supervisor's close-out summary, `closed.json`
  and the live view's plan chapter say the run was skipped under the posture, naming the source and the reason.
- Nothing runs instead. There is no lighter close-out check to configure.
- `lead-close` holds nothing new. Under `focused` it records the skip the same way when no full-health result exists. Under `gate` it does what it does today.
- `orchestration-binding/1` doesn't change. `fullHealth` stays required, so restoring `gate` needs no binding change.
- `orchestrate-work`'s guide states the same rule for a delivery without the supervisor.

### Two repositories

The runtime's source is `kwam1na/agent-delivery-harness`. `product-runtime/` is its build output, and today it matches a build of that repository's `fa63884` byte for
byte. So V26-3124 has two change requests:

1. **The source,** a pull request to the harness's `main`, bumping the kernel and CLI packages to 0.7.8. That repository's main CI has been red since before this work
   and requires no check, so the lane's evidence is `npm run check` compared against `main`'s own result. The lead merges it, once reviewed.
2. **The build,** `product-runtime/` rebuilt with `npm run product:build-runtime` from a clean checkout of a branch `runtime/0.7.8` in that repository, which is
   `fa63884` plus the merged change cherry-picked. The commit it was built from is named in the change request and in `docs/product-generation.md`. This keeps V26-2347's
   unfinished commits out of what adopters run. A later runtime build from `main` picks up both.

Each lane's hand-off through the queue is the second change request, for V26-3124. The first is outside the queue because the binding serves only this repository.

### Order

The tickets record V26-3125 as blocked by V26-3124 because it settled the declaration. This plan settles it, so both start now. `tests/test_run_delivery.py` pins the
runtime version and lane 2 owns that file, so V26-3125 derives the pin from `product-runtime/runtime.json` and merges first. V26-3124's build is handed off after that.

### A base that is already red

CI's Ubuntu job on `fe6ff2f` failed one test,
`SupervisorProcessTests.test_a_running_supervisor_restarts_its_children_and_reports_one_that_wont_stay_up`, with `KeyError: 'children'`. The test reads the lock file
before the supervisor has written its children. Earlier commits passed that job. V26-3125 owns that suite and fixes the race, since the finish line needs that job
green. CI's macOS job has timed out in `tests.test_run_delivery` for several commits. It informs and doesn't gate, so it stays out of this delivery.

## Stories

### V26-3124. Let a repository declare its delivery posture, focused or gate, in the harness

- Lane: lane-1. Not blocked.
- Files in the harness repository: `packages/kernel/src/`, `packages/cli/src/commands/verify.ts`, `gate.ts`, `record.ts`, `resume.ts`, their tests, the package versions, the docs.
- Files here: `product-runtime/**`, `docs/product-generation.md`, `README.md`, `skills/bootstrap-agent-harness/**`, `tests/test_product_release.py`, `tests/test_product_descriptors.py`, `tests/test_product_launcher.py`, `tests/built_product.py`.
- Scenarios, written first in the harness's suites: base `focused`, `verify` and `resume` report the focused outcome; base `gate`, candidate `focused`, still gated; a
  candidate changing `harness.config.ts`, still gated with `posture_config_changed`; a malformed declaration, gated with the named code; no declaration, existing fixtures
  unchanged; every row of the resolution table.
- Scenario here: a built product's `harness verify`, with `focused` on a fixture's base and no record, exits 0 and names the posture and source.
- Sensors: `npm run check` in the harness repository; here, the listed test modules and `tests/test_cut_release.py`.
- Adoption notes: the file, its schema, the fallback table, and a mapping from Athena's local version to this one covering V26-3124's fifth acceptance criterion.
  Compounding: record what Athena's use since 2026-11-04 needed.

### V26-3125. Make run-delivery's supervisor honour the focused posture at close-out

- Lane: lane-2. Not blocked.
- Files: `skills/run-delivery/**`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/**`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_delivery_views*.py`.
- Scenarios, written first: every one of V26-3125's six; every row of the resolution table; a skip never reads as a pass in the journal, the close-out summary or the
  plan chapter; the supervisor test's race, fixed so that a read before the children are written retries.
- Sensors: `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_tracker.py`, `tests/test_delivery_views_render.py`.
- Compounding: record what, if anything, a broad run would have caught on V26-2985 and V26-3111.
