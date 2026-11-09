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

- Cutting or applying a release. The 0.8.3 runtime and the reader changes ship with the next batch release.
- The other backlog epics: lead hand-work (V26-3250), release safety (V26-3266), the harness runtime (V26-3268), and the suite work live
  in V26-3304.
- Commits on the harness's `main` after `v0.8.2` that belong to other work. They stay out of the runtime this delivery ships.
- Athena. It reads the posture through the exported resolver, so it picks up the fix with its next runtime update.

## Decisions

1. **Deep nesting is malformed past depth 64.** A declaration whose JSON nests arrays or objects more than 64 levels deep reads as `gate`
   with `posture_malformed`, in both readers, on every Python and Node version. The top-level object is depth 1. Accepting any depth isn't
   an answer both readers can give: Python 3.11's decoder raises `RecursionError` near 1000 levels, and a 64 KiB blob can nest about 32,000
   levels, deeper than any recursion limit Python can safely raise. No declared member needs depth, and members other than the three
   required ones are ignored. Each reader measures depth on the raw text before it parses, so no decoder's limit is ever reached. Depth is
   the greatest number of arrays and objects open at once: it goes up by one at each `[` or `{` and down by one at each `]` or `}`, outside
   strings. A string starts and ends at an unescaped `"`, and inside one a `\` escapes exactly the next character, so `\"` doesn't end the
   string and `\\"` does. Malformed text of any depth is still `posture_malformed`. The shared vectors, each a valid `focused` declaration
   around the case:
   - one member nested 64 deep: accepted; 65 deep: malformed; 1500 deep (the ticket's case): malformed;
   - the deepest nesting a 64 KiB blob can hold (about 32,000 levels, under the size bound): malformed. This pins agreement on the answer
     at the extreme, not the order of operations: a reader that parses and then walks can give the same answer;
   - 65 or more sibling arrays at depth 2: accepted; 65 or more sibling objects at depth 2: accepted;
   - two sibling members, the one nesting objects first and the one nesting arrays second, each 64 deep: accepted;
   - a string holding 65 `[` with no backslash: accepted; a string holding closing brackets at depth 64: accepted;
   - a string holding an escaped quote followed by 65 `[`: accepted;
   - a string ending in an escaped backslash (`"r\\"`) followed by a member nested 65 deep: malformed.

   Each reader's red runs are recorded against five mutants: a counter that never decrements, one that decrements only on `]`, one that
   decrements only on `}`, a scanner that ignores escapes, and one that treats a quote as escaped whenever a backslash precedes it.
2. **One harness change, one runtime build.** V26-3162's root rule and the JavaScript half of Decision 1 land in one pull request to the
   harness's `main`, which bumps the kernel and CLI packages to 0.8.3. The lead merges it once reviewed. So V26-3162's story carries the
   resolver half of V26-3163, and V26-3163's story here carries the Python half, the vectors and the wording pin.
3. **The runtime is built on the last harness release.** `product-runtime/` is rebuilt with `npm run product:build-runtime` from a clean
   checkout of a harness branch `runtime/0.8.3`: `v0.8.2`'s commit plus the merged change cherry-picked. The build's change request and
   `docs/product-generation.md` name the commit it was built from, next to the runtime's digests, and say that 0.8.3 carries 0.8.2's
   warm-base prewarm (#200). (Plan change, 2026-11-09: V26-3294 released #200 as the tagged `v0.8.2` after this plan was approved, so 0.8.2
   is taken and #200 is no longer unfinished work; the change ships as 0.8.3 on top of it.) Nothing after `v0.8.2` but the cherry-pick
   ships. The build's evidence proves that three ways: `git log --format=%H v0.8.2..runtime/0.8.3` lists only the cherry-picked commit,
   and its diff equals the merged pull request's; a second clean build from `runtime/0.8.3` reproduces `product-runtime/` byte for byte;
   and a build from `v0.8.1` plus the cherry-pick does not, the planted failure that shows the comparison can fail.
4. **Repository root only.** The resolver resolves only when all three hold for the directory it was given; otherwise it is `gate` with
   `posture_unreadable`, naming the directory:
   - `git rev-parse --show-toplevel` succeeds and prints that directory. Inside a bare repository it fails, so a bare layout gates whatever
     `safe.bareRepository` says or whichever git is installed.
   - `git rev-parse --show-superproject-working-tree` prints nothing, so an initialised submodule gates.
   - The directory's parent is not inside a work tree (`git -C <dir>/.. rev-parse --is-inside-work-tree` doesn't print `true`), or it is,
     and that enclosing work tree's `--git-common-dir` is the directory's own, both resolved to canonical real paths, symlinks followed, before
     they are compared (git prints it relative from a subdirectory, and macOS reaches temporary folders through `/var`). That gates a real nested repository and an untracked gitfile
     pointing at another repository. It keeps a linked worktree nested in its own main checkout resolving, as lane checkouts at
     `.orchestration/lanes/<id>` and Athena's `.worktrees/<id>` are, and those read the same `origin` as the root.

   Every git call runs with the resolver's scrubbed local environment. The accepted consequence: a repository cloned inside another
   repository's work tree, and not a linked worktree of it, gates. From a root, every vector's answer is unchanged. `run_delivery.py`'s reader
   already reads the delivery's own repository by path, so it needs no change for V26-3162. V26-3162's built-product rows here prove the
   routes through the built CLI as well.
5. **Triage against `main`, then fix or drop.** V26-3174's lane checks each finding against `main` first and records, per finding, the
   commit or test that already settles it, or its fix. A finding is dropped only with a reason the lead accepts at hand-off (it no longer
   applies, or another merged change settles it); nothing is filed. Where a finding names a mutation or an unguarded branch and the lane
   says an existing test or commit settles it, the lane records that test's red run by name under the finding's own mutation, then the
   revert. A finding with no runnable mutation records why. The lead accepts a drop only with that record. The two behaviour items, which
   may already be settled on `main`, are checked the same way: `lead-resolve full-health` doesn't promise a rerun while the base declares
   `focused`, saying instead that the rerun waits for `gate`; and `rebuild_plan`'s "moved" text names a member removed from the plan, such
   as `closeOut`. For the hanging-git stubs, the lane names the pattern the finding means, renames the stubs' sleep so it doesn't match, and
   adds a row asserting that no stub's command line matches that pattern, whatever PR 206 did.
6. **Beside V26-3304 and V26-3250.** Both are live on the same `main`. V26-3304's lane 1 owns `skills/run-delivery/`,
   `tests/test_run_delivery.py` and `provenance.lock.json` in its delivery; V26-3250 changes `run_delivery.py`, its references, the
   orchestrate-work docs, `tests/test_run_delivery.py` and the lock; lane 2 here changes the same paths. The three leads agreed on
   2026-11-08:
   - V26-3304's V26-3286 and V26-3273 merge first. They rewrite `clean_environment()`, `Fixture.__init__`, `Config` and `main_checkout`
     resolution, `Supervisor.run`, `start_supervisor`, `stop_children` and `SupervisorProcessTests`, so lane 2 stays out of those until they
     merge.
   - After that, whichever delivery is ready. No lead queues a candidate on a shared file while another delivery's candidate touching it is
     queued, and each lead messages the others before queuing one and when a story merges.
   - `provenance.lock.json` is regenerated by its writer at sync, never merged by hand.
   - Each lead works in its own worktree, never on the operator's main checkout.

   The lane never merges the base. When a sync conflicts outside the lock, the lead decides: requeue after the other candidate lands, or have
   the lane rebase its branch onto `main` and carry or rerun its review as `lead-carry-review` allows. Lane 1's files are this delivery's
   alone, so it queues without waiting.

## Units and order

Two lanes.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3162 | the harness change and `runtime/0.8.3`; here `product-runtime/`, `docs/product-generation.md`, `README.md`, `tests/test_product_release.py`, `tests/test_product_descriptors.py`, `tests/test_product_launcher.py`, `tests/test_cut_release.py`, `tests/built_product.py`, `tests/test_delivery_posture_vectors.py`, `tests/fixtures/delivery-posture-vectors.json` |
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
  `posture_unreadable`, with the root's base declaring `focused` too, so neither a root read nor a nested read would gate.
- From a standalone bare clone in its own temporary directory, whose parent is in no work tree and whose `refs/remotes/origin/main`
  declares `focused`, under a git that emulates one before 2.38: a `git`
  wrapper ahead of the real one on `PATH` drops the resolver's `-c safe.bareRepository=explicit` pair from its arguments and runs the real
  git with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`. The row first confirms that, under the wrapper, the bare repository's
  `rev-parse --verify refs/remotes/origin/main` succeeds, then asserts `gate` with `posture_unreadable`. Red run under the wrapper with the
  show-toplevel failure branch mutated to carry on. The same row without the wrapper gates too, by git's own refusal. A second row reads
  from a bare repository embedded in the tree and gates: unmutated, by the first condition (git skips the embedded repository or fails
  inside it), and by the third if the first is mutated away; it claims no red run against the show-toplevel mutant.
- Allowed, with the base declaring `focused`, read from each one's own root: a normal clone whose parent is no work tree; a linked worktree
  made with `git worktree add` outside its main checkout; and one nested under its main checkout at `.orchestration/lanes/<id>`. Each reads
  `focused` with no code, including when its root is given through a symlinked path such as macOS's `/var`. The nested worktree's red runs are recorded against two mutants: refusing whenever `.git` is a file, and refusing
  whenever the parent is inside a work tree, without the common-directory check.
- From a root, every existing scenario and vector gives what it gave before.
- The surviving mutants fail a test: each of the two `cat-file` guards removed, the environment dropped from the URL read and from the local
  read, and the CLI's interrupt check removed. The lane records each mutant's red run by test name.
- `--no-auto-maintenance` and `--no-recurse-submodules` each removed from the fetch: a test fails.
- Every depth vector Decision 1 lists, with the red runs it names for the JavaScript reader.
Evidence on the harness follows V26-3124's: `npm run typecheck`, `npm run sensor`, `npm run sensor:cli` and `npx vitest run` as separate
steps on `main` before the work and on the pull request, compared per test, with no new failure and the new scenarios passing by name; and
the same on `runtime/0.8.3` against `v0.8.2`. Here, the build per Decision 3, the literal `0.8.3` pin in `tests/test_product_release.py`,
`tests/test_cut_release.py`'s version from the descriptor, and three built-product rows in `tests/test_delivery_posture_vectors.py`, each
with the consumer root's base declaring `focused`. In the first, `verify` is given a plain subdirectory of the root, with no repository of
its own: it is gated, and its stderr carries the `posture_unreadable` line ahead of the command's blocker. Its red run is recorded against a
mutant that normalises the directory to `--show-toplevel` before resolving. In the second, a nested repository's `origin` declares
`focused` as well, and `verify` run from the nested repository's directory is gated the same way. Its red run is recorded against a mutant
that drops the parent-work-tree condition. In the third, `verify` run from a linked
worktree nested under the consumer's root, with its base declaring `focused`, exits 0 with the `focused` outcome. The vector file runs
against the built runtime's export. The deep-nesting vectors go into the vector file in V26-3163, not here, so the vector test stays green
on both readers at this merge. Sensors: the harness's steps above; here `tests/test_delivery_posture_vectors.py`,
`tests/test_product_release.py`, `tests/test_cut_release.py`, `tests/test_product_descriptors.py`, `scripts/validate-registry.py`.
The hand-off carries Decision 3's three build records. Adoption notes: `docs/product-generation.md` names the build commit, its digests and
what 0.8.3 adds.

### V26-3174. Triage the deferred review findings from the delivery posture work

Lane 2, tier 1, not blocked. Each of the 22 findings in the item, per Decision 5: checked against `main`, then fixed or dropped with a
reason, and listed in the hand-off with its outcome and the test or commit that shows it. The run-delivery findings first. The built-product
findings (V26-3124's list: the no-record snapshot row, the self-publishing `baseRef` row, `DeliveryPostureResult` in `kernel.d.mts`'s
`export type` list, `HARNESS_VERSION` pinned to the descriptor's `runtimeVersion`, the README anchor pinned to the guide's heading, the
plain-`main` row, and the three `RuntimeTrustTests` rows) wait until V26-3162 merges and its files move to lane 2, and run against the 0.8.3
runtime. Each new pin is proved red once by mutating what it pins, with the digest refreshed alongside so the pin's own assertion is what
fails, then restored. Files: lane 2's `owns`, and the moved files once moved. Sensors: `tests/test_run_delivery.py`,
`tests/test_run_delivery_supervisor.py`, `tests/test_delivery_posture_vectors.py`, `tests/test_product_release.py`,
`scripts/validate-registry.py`, `scripts/update-provenance.py --check`.

### V26-3163. Make both posture readers agree on deeply nested declarations

Lane 2, tier 1, after V26-3174 and V26-3162. Test-first. `posture_of_bytes` in `run_delivery.py` measures depth per Decision 1 before it
parses, so `RecursionError` is never what decides. Every depth vector Decision 1 lists goes into
`tests/fixtures/delivery-posture-vectors.json`, and the vector test runs them against both readers. The lane records the red run of the
depth-1500 and depth-65 vectors on the unfixed Python reader under Python 3.14, where it accepts both; the red runs Decision 1 names against
the three scanner mutants; and the green run on 3.11 and on 3.14, with `.venv-<name>` built by the bootstrap's `--environment`. The
`lead-resolve` wording under `focused` in `skills/run-delivery/SKILL.md` and `references/config.md`, as V26-3174 leaves it, is pinned by a
corpus pin each in `tests/test_run_delivery.py`, proved red. Files: `skills/run-delivery/`, `tests/test_run_delivery.py`, the moved vector files,
`provenance.lock.json`. Sensors: `tests/test_delivery_posture_vectors.py` on 3.11 and 3.14, `tests/test_run_delivery.py`,
`scripts/validate-registry.py`.

## Execution posture

`test-first` for all three. Lanes work to the focused lane profile: no full suite in a lane. The queue runs the selected checks on the synced
head, and the lead runs the full suite on macOS with Python 3.11 at the close.

## Approval handoffs

- The plan itself, before `lead-setup`.
- Merging the harness pull request and pushing `runtime/0.8.3`, both outside this repository's queue. The operator granted the lead these
  approvals for this delivery on 2026-11-08.

## Tracking

The epic and its three items are in Linear. The lead posts at each milestone with the host's Linear tools and moves items to their final
state.
