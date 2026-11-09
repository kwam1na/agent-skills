---
reviewSlug: posture-readers
---

# Make every delivery-posture reader agree

Epic V26-3267: three items left from the posture work (V26-3126). `.agents/delivery-posture.json` has two readers, `run_delivery.py`'s and the
harness's `resolveDeliveryPosture`, and every reader must give the same answer for the same bytes, from the same place. Each item's tracker
scope binds what it does. This plan decides what the items leave open: the deep-nesting answer, which repository each change lands in, file
ownership, order, and how this delivery runs beside V26-3304, which is live on the same base.

## Finish line

- Each item is merged to `main` of `kwam1na/agent-skills` through the merge queue, or closed with a recorded reason. V26-3162's source is
  merged to `main` of `kwam1na/agent-delivery-harness`, and its built runtime reaches `main` here through the queue.
- The shared vector file, `tests/fixtures/delivery-posture-vectors.json`, carries the deep-nesting vectors, and both readers give the same
  posture, code and source on every vector: run here on Python 3.11 and on 3.14 against `run_delivery.py`'s reader and the built runtime's
  exported resolver.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- The full local suite passes on macOS with Python 3.11 at that commit.
- No finding is deferred. Every finding any review in this delivery raises, at any severity, is fixed in the story that found it, and no
  follow-up item is filed for it, as the operator ruled for V26-3304 on 2026-11-08. This epic exists to clear the last delivery's deferrals,
  so it takes the same rule. Each of V26-3174's 22 findings is fixed or dropped with a reason; none is filed.

## Out of scope

- Cutting or applying a release. The 0.8.2 runtime and the reader changes ship with the next batch release.
- The other backlog epics: lead hand-work (V26-3250), release safety (V26-3266), the harness runtime (V26-3268), and the suite work live
  in V26-3304.
- Commits on the harness's `main` after `v0.8.1` that belong to other work, such as V26-3294's warm-base prewarm (#200), still in progress
  under V26-3204. They stay out of the runtime this delivery ships.
- Athena. It reads the posture through the exported resolver, so it picks up the fix with its next runtime update.

## Decisions

1. **Deep nesting is malformed past depth 64.** A declaration whose JSON nests arrays or objects more than 64 levels deep reads as `gate`
   with `posture_malformed`, in both readers, on every Python and Node version. The top-level object is depth 1. Accepting any depth isn't
   an answer both readers can give: Python 3.11's decoder raises `RecursionError` near 1000 levels, and a 64 KiB blob can nest about 32,000
   levels, deeper than any recursion limit Python can safely raise. No declared member needs depth, and members other than the three
   required ones are ignored. Each reader measures depth on the raw text, counting `[` and `{` outside strings (with escapes honoured), and
   refuses before it parses, so no decoder's limit is ever reached. Malformed text of any depth is still `posture_malformed`. The vectors:
   depth 64 accepted, depth 65 malformed, depth 1500 malformed (the ticket's case), and depth 65 inside a string accepted, each with a valid
   `focused` declaration around it.
2. **One harness change, one runtime build.** V26-3162's root rule and the JavaScript half of Decision 1 land in one pull request to the
   harness's `main`, which bumps the kernel and CLI packages to 0.8.2. The lead merges it once reviewed. So V26-3162's story carries the
   resolver half of V26-3163, and V26-3163's story here carries the Python half, the vectors and the wording pin.
3. **The runtime is built without other work.** `product-runtime/` is rebuilt with `npm run product:build-runtime` from a clean checkout of a
   harness branch `runtime/0.8.2`: `v0.8.1`'s commit plus the merged change cherry-picked. The build's change request and
   `docs/product-generation.md` name the commit it was built from. This keeps #200 out of what adopters run, as V26-3124 kept V26-2347's
   commits out of 0.7.8. A later runtime build from `main` picks up both. Two runtimes then declare 0.8.2, which is why the change request
   names the commit and the digests, never the version alone.
4. **Repository root only.** The resolver runs `git rev-parse --show-toplevel` and `--show-superproject-working-tree` against the directory
   it was given, and resolves only when that directory is the top level of a repository that is not itself a submodule. A subdirectory, a
   nested repository's directory, an initialised submodule and an untracked gitfile in a subdirectory resolve to `gate` with
   `posture_unreadable`, naming the directory. The bare-layout refusal holds on git older than 2.38, where `safe.bareRepository` is ignored,
   because the rule doesn't depend on it. From the root every vector's answer is unchanged. `run_delivery.py`'s reader already reads the
   delivery's own repository by path, so it needs no change for V26-3162; V26-3162's built-product rows here prove the subdirectory routes
   gate through the built CLI as well.
5. **Triage against `main`, then fix or drop.** V26-3174's lane checks each finding against `main` first and records, per finding, the
   commit or test that already settles it, or its fix. A finding is dropped only with a reason the lead accepts at hand-off (it no longer
   applies, or another merged change settles it); nothing is filed. The two behaviour items are fixed: `lead-resolve full-health` stops
   promising a rerun while the base declares `focused`, saying instead that the rerun waits for `gate`; and `rebuild_plan`'s "moved" text
   names a member removed from the plan, such as `closeOut`. The hanging-git stubs' sleep is renamed so no pattern a test or an operator
   would use to find stray processes matches it, whatever PR 206 did.
6. **Beside V26-3304.** V26-3304's lane 1 owns `skills/run-delivery/`, `tests/test_run_delivery.py` and `provenance.lock.json` in its own
   delivery, and lane 2 here changes the same paths. The two deliveries' queues merge into the same `main`. The lane never merges the base;
   the queue syncs at merge time, and `provenance.lock.json` is regenerable. When a sync conflicts outside it, the lead decides: requeue after
   V26-3304's candidate lands, or have the lane rebase its branch onto `main` and carry or rerun its review as `lead-carry-review` allows. The
   lead doesn't queue a V26-3267 candidate while a V26-3304 candidate touching the same file is in the queue.

## Units and order

Two lanes.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3162 | the harness change and `runtime/0.8.2`; here `product-runtime/`, `docs/product-generation.md`, `README.md`, `tests/test_product_release.py`, `tests/test_product_descriptors.py`, `tests/test_product_launcher.py`, `tests/test_cut_release.py`, `tests/built_product.py`, `tests/test_delivery_posture_vectors.py`, `tests/fixtures/delivery-posture-vectors.json` |
| 1 | 2 | V26-3174, then V26-3163 | `skills/run-delivery/`, `skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `provenance.lock.json`, `docs/solutions/` |

**Moving ownership.** When V26-3162 merges, the lead moves `tests/test_delivery_posture_vectors.py`, `tests/fixtures/delivery-posture-vectors.json`,
`tests/test_product_release.py` and `tests/built_product.py` to lane 2, which needs them for V26-3174's built-product rows and for V26-3163's
vectors. Until then lane 2 works V26-3174's run-delivery findings and leaves the built-product ones. Lane 1 changes no listed output, so it
never needs `provenance.lock.json`; if a lane finds it needs a file outside its `owns`, it raises it with the lead before hand-off.

## Stories

### V26-3162. Read the delivery posture only from the repository root

Lane 1, tier 1, not blocked. Test-first, per the item. In the harness, the resolver follows Decision 4, and Decision 1's depth bound goes in
`resolveDeliveryPosture`'s byte reader. Scenarios, written first in `packages/kernel/src/delivery-posture.test.ts` and the CLI's tests:
- From a subdirectory of the root, a real nested repository whose `origin` declares `focused`, an initialised submodule whose `origin`
  declares `focused`, and an untracked gitfile in a subdirectory pointing at a repository declaring `focused`: each `gate` with
  `posture_unreadable`, with the root's base declaring `focused` for the first two so a root read would differ.
- The same routes on a git older than 2.38, or with `safe.bareRepository` unset where the host's git is older: `gate`.
- From the root, every existing scenario and vector gives what it gave before.
- The surviving mutants fail a test: each of the two `cat-file` guards removed, the environment dropped from the URL read and from the local
  read, and the CLI's interrupt check removed. The lane records each mutant's red run by test name.
- `--no-auto-maintenance` and `--no-recurse-submodules` each removed from the fetch: a test fails.
- Depth 64 accepted, depth 65 and depth 1500 `posture_malformed`, depth 65 inside a string accepted.
Evidence on the harness follows V26-3124's: `npm run typecheck`, `npm run sensor`, `npm run sensor:cli` and `npx vitest run` as separate
steps on `main` before the work and on the pull request, compared per test, with no new failure and the new scenarios passing by name; and
the same on `runtime/0.8.2` against `v0.8.1`. Here, the build per Decision 3, the literal `0.8.2` pin in `tests/test_product_release.py`,
`tests/test_cut_release.py`'s version from the descriptor, and built-product rows in `tests/test_delivery_posture_vectors.py`: the built
CLI's `verify` run from a subdirectory holding a nested repository whose `origin` declares `focused` stays gated, and the vector file runs
against the built runtime's export. The deep-nesting vectors go into the vector file in V26-3163, not here, so the vector test stays green
on both readers at this merge. Sensors: the harness's steps above; here `tests/test_delivery_posture_vectors.py`,
`tests/test_product_release.py`, `tests/test_cut_release.py`, `tests/test_product_descriptors.py`, `scripts/validate-registry.py`.
Adoption notes: `docs/product-generation.md` names the build commit and what 0.8.2 adds.

### V26-3174. Triage the deferred review findings from the delivery posture work

Lane 2, tier 1, not blocked. Each of the 22 findings in the item, per Decision 5: checked against `main`, then fixed or dropped with a
reason, and listed in the hand-off with its outcome and the test or commit that shows it. The run-delivery findings first. The built-product
findings (V26-3124's list: the no-record snapshot row, the self-publishing `baseRef` row, `DeliveryPostureResult` in `kernel.d.mts`'s
`export type` list, `HARNESS_VERSION` pinned to the descriptor's `runtimeVersion`, the README anchor pinned to the guide's heading, the
plain-`main` row, and the three `RuntimeTrustTests` rows) wait until V26-3162 merges and its files move to lane 2, and run against the 0.8.2
runtime. Each new pin is proved red once by mutating what it pins, with the digest refreshed alongside so the pin's own assertion is what
fails, then restored. Files: lane 2's `owns`, and the moved files once moved. Sensors: `tests/test_run_delivery.py`,
`tests/test_run_delivery_supervisor.py`, `tests/test_delivery_posture_vectors.py`, `tests/test_product_release.py`,
`scripts/validate-registry.py`, `scripts/update-provenance.py --check`.

### V26-3163. Make both posture readers agree on deeply nested declarations

Lane 2, tier 1, after V26-3174 and V26-3162. Test-first. `posture_of_bytes` in `run_delivery.py` measures depth per Decision 1 before it
parses, so `RecursionError` is never what decides. The four depth vectors go into `tests/fixtures/delivery-posture-vectors.json`, and the
vector test runs them against both readers. The lane records the red run of the depth-1500 and depth-65 vectors on the unfixed Python reader
under Python 3.14, where it accepts both, and the green run on 3.11 and on 3.14, with `.venv-<name>` built by the bootstrap's
`--environment`. The `lead-resolve` wording under `focused` in `skills/run-delivery/SKILL.md` and `references/config.md`, as V26-3174
leaves it, is pinned by a corpus pin each, proved red. Files: `skills/run-delivery/`, `tests/test_run_delivery.py`, the moved vector files,
`provenance.lock.json`. Sensors: `tests/test_delivery_posture_vectors.py` on 3.11 and 3.14, `tests/test_run_delivery.py`,
`scripts/validate-registry.py`.

## Execution posture

`test-first` for all three. Lanes work to the focused lane profile: no full suite in a lane. The queue runs the selected checks on the synced
head, and the lead runs the full suite on macOS with Python 3.11 at the close.

## Approval handoffs

- The plan itself, before `lead-setup`.
- Merging the harness pull request and pushing `runtime/0.8.2`, both outside this repository's queue. The operator granted the lead these
  approvals for this delivery on 2026-11-08.

## Tracking

The epic and its three items are in Linear. The lead posts at each milestone with the host's Linear tools and moves items to their final
state.
