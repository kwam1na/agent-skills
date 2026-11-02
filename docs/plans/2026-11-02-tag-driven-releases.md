---
title: "feat: Make a release one annotated tag, with the machine doing the rest"
type: feat
status: approved
date: 2026-11-02
execution_posture: test-first
context: One four-line behaviour change took a 30-file release pull request, hand-edited pins in eight test files and a live Linear run. Several repositories now adopt the corpus, no adopter reads qualifications from this repository, and CI on the default branch has been red for about two months without anything stopping on it.
---

# feat: Make a release one annotated tag, with the machine doing the rest

## Outcome

The owner, or an agent the owner authorizes, cuts a release by pushing one annotated version tag. From the tagged tree,
the release workflow runs the repository's checks, builds and qualifies the products, and publishes them with their
records and build provenance. If anything fails, nothing is published. No release pull request exists, no release
edits a file in the repository, and no release needs a live tracker run. CI on the default branch is green and means
something, because publishing depends on it. Orchestrated lanes always run as sessions.

## Evidence behind this plan

- **Adopters.** Athena has the `linear` profile installed under its own release id, so the Linear profile stays
  qualified offline; only the live gate goes. Neither Athena nor the harness reads this repository's
  `qualifications/`: their references are to the harness's own records.
- **Release ids.** `release.py` requires only a non-empty string, and the runtime accepts dotted ids, so the version
  scheme needs no lifecycle change.
- **Runtime rebinding.** `build_release` refuses a descriptor whose `workflowContentSha256` does not match the
  profile's projection. Rebinding has been done outside the build, by hand per release and in test helpers.
- **Runtime identity.** `runtimeVersion` does not identify runtime bytes. Athena's active generation and the
  `draft-building-v1` products both declare 0.7.7, yet their `kernel.mjs`, `kernel.d.mts`, `cli.mjs` and `cli-api.mjs`
  differ: Athena's carries the installed-data protocol its own scripts consume. `apply` has no runtime-version ordering.
- **Repository.** It is public, so build provenance is available. Immutable releases are available and currently off.
- **The live run.** The qualifier checks an attestation's shape, order and bindings; the README already says it does
  not independently prove the external calls happened. A connector also breaks on Linear's or the host's schedule, not
  on this repository's tags.
- **CI.** The default branch's last fully green run was 2026-09-03. Windows last concluded on 2026-09-10 with failure,
  macOS last concluded on 2026-09-03 with failure, and neither has completed since, so the current failing set on both
  is unknown. Since 2026-10-22 the sampled failures are on Ubuntu: bytecode written under
  `skills/orchestrate-work/scripts/` fails corpus validation with `provenance.coverage`, cascading into 181 errors, and
  the page lane's packages are never installed. The default fail-fast then cancels the other systems.
- **Skill text and checked archives.** The drift tests compare checked archives with source, so today any skill change
  on the default branch is red until a full release follows it. That is why a text change cannot land alone.

## Contract decisions

Every unit is bound by these.

- **Delivery base.** Lanes branch from, and their change requests target, `delivery/tag-driven-releases`, cut from the
  default branch. One integration change request lands it, so the default branch never holds a partial migration.
- **Lanes.** Each lane is a session. Its two review lenses are subagents of that session, in their own checkouts.
- **Runner.** The repository has no orchestration binding, and adding one is out of scope. The lead performs merges and
  writes the runner's fields, following the guide's path for a repository without the runner, and journals that.
- **Expected red.** The delivery base may be red between merges, because units remove one another's stale pins. The
  guide's broken-base stop does not apply to an expected red. After each merge the lead journals the failing-test set,
  and every failure new to that set must name the unit that disposes of it, or it is a real regression and returned.
- **Runtime.** `product-runtime/` holds the 11 runtime files, seeded from Athena generation
  `5463b30e16a41e23c5aff4d189b68da2d6f200df3549a6354543db9fa959feb7` (release `linear-athena-installed-data-v1`, Athena
  commit `1c1e34eac`, producer harness commit `fa63884c5dfc824b8076fc9f33deba05d577c4d2`), not from `draft-building-v1`,
  so the first version tag cannot silently remove a protocol an adopter uses. Every seeded file matches the frozen list
  below, and `runtime.json` differs from that generation's only in `workflowContentSha256`, if at all. A later runtime is
  an ordinary reviewed change request and does not alter this seed record.
- **Frozen seed digests** (sha256, from that generation's `runtime.json`, each verified against the file):
  - `LICENSE` `02ed546806a3298b12c633742eb2fd354821d5dc9a5ee4384d4ae8196737a83f`
  - `NOTICE` `547c61e2d762d0cadff8c1560ac7b9c072d4d71315797e11629654079ce0882d`
  - `bootstrap.mjs` `5df698a9e5d7bae942ec5a40819ba52b0688cab87c653ee8b42e30160ad1225a`
  - `cli-api.d.mts` `4990e957399ec8c28fc79c678533994f1cae84c5bdfb1036d33192ce03d7f0d9`
  - `cli-api.mjs` `35d279f297d0c251acaece21b68d6e0273c1b6fedf2a0e5bfe7153052d1b1717`
  - `cli.mjs` `14f76e4fb5d0a42d0b6709b32de52c5ba4ee33312bb852f8f72b1795f23aee57`
  - `kernel.d.mts` `af8aa41a84ddc263672565f6c38a09928d94cc02186772bb9204f7b77566512b`
  - `kernel.mjs` `9283a01c51e72416ea70388ff33ca38cc1a534b626670caa3189dfa71ad317b4`
  - `loader.mjs` `d00b40605a54f7defd9b8cab05825009af57baf2d5884d736eddc391ba35a1e8`
  - `policy.mjs` `320aa341d55833061c08b4c9e47f998c341229e964e84bebbf9db8da875a0d24`
- **Rebinding.** `build_release` and `verify_release` stay unchanged and keep refusing a mismatched descriptor.
  `cut-release` copies `product-runtime/` to a per-profile temporary directory, rewrites only the
  `workflowContentSha256` value by exact text replacement, and passes that directory to `build_release`.
- **`cut-release` command line.** `scripts/cut-release.py --version <version> --out <directory>`. The directory holds
  exactly five archives, five release files and four records, named `<profile>-<version>.zip`,
  `<profile>-<version>.release.json`, and `core-<version>.json`, `provider-<version>.json`, `bootstrap-<version>.json`
  and `linear-<version>.json`. It lives outside the checkout or is ignored, so a run leaves `git status` unchanged.
- **Linear qualifier command line.** `qualify-linear.py --core-archive --core-metadata --core-record --linear-archive
  --linear-metadata --record`, with no binding or attestation input.
- **Versions.** Tags are `vMAJOR.MINOR.PATCH`, optionally `-rc.N`, with no leading zeros. Major means an adopter must
  act, minor is additive, patch changes no contract. Release ids are `<profile>-<version>`.
- **Ordering guard.** Before anything is built, a tag's version must be strictly greater, in semantic order with a
  candidate below its final, than every existing version tag, and the tagged commit must equal or descend from the
  commit of the highest existing version tag. Backports to an older line are unsupported until separately planned.
- **Latest.** A final publishes with explicit `--latest`, a candidate with `--prerelease --latest=false`.
- **Permissions.** `release.yml` sets workflow-level `permissions: {}`. The job calling `ci.yml` grants only
  `contents: read`. Only the publish job holds `contents: write`, `id-token: write` and `attestations: write`. `ci.yml`
  declares nothing above `contents: read`.
- **Publish environment.** The publish job installs `.[test]` on `ubuntu-latest` with the same Python version as CI's
  `cut-release` job, and calls `cut-release` the same way.

## In scope, by unit

Each unit is one tracked item. Owned files are exclusive: a lane needing another unit's file asks the lead.

### Unit: Green CI

- **Outcome.** Every operating system's failure is known and has an owner, and Ubuntu is green. All three systems passing
  is proven at the integration change request, not here.
- **Owns.** `.github/workflows/ci.yml`; `tests/test_qualification_pending.py` for one method only; a test or workflow fix
  a failure needs, with the lead handing over any file another lane owns.
- **Scope.** No bytecode is written during CI runs, the page lane's packages are installed, and `fail-fast` is off so every
  operating system reports. The `edited` trigger, which existed for the marker, goes. The validate step stops passing
  `--target-base` and its `TARGET_BASE` environment block goes, which is safe while `validate-registry.py` still accepts
  the argument. This unit also deletes `test_ci_passes_the_target_base_for_default_branch_events` from
  `tests/test_qualification_pending.py`, handed over by the lead; the current-set unit removes the rest of that file later.
- **Scout first.** The lane opens a draft change request with `fail-fast` off, and the lead journals every failing test per
  operating system. Each failure is then classified: a test another unit removes is left for that unit and named against
  it; a test or workflow fix is made by this unit; shipped code or a skill file is a lead decision recorded as an addition
  to scope, lands only after the drift-test units merge, and takes its provenance refresh through the lane ruling's owner.
- **Stop.** If a failure needs a change this plan excludes, or cannot be made green, the lead stops and brings the operator
  one question with a recommendation. The matrix stays a required gate, and no operating system is dropped or skipped
  without the operator's ruling.
- **Depends on.** Nothing. It runs before any other lane starts, because its scout list routes work to them.
- **Required scenarios.** CI. **Sensors.** Its change request's CI.

### Unit: Offline Linear qualification

- **Outcome.** `qualify-linear.py` keeps core binding, install and host discovery, controlled degraded outcomes and
  forbidden transport under a new record schema version. The collector input, the attestation and their live scenarios
  go, with their code and tests. Linear's core projection must still match the separately qualified core, with the
  graph digest taken from the candidates. Its replay test builds both archives from the tree without a runtime, which the
  qualifier supports, and `test_product_projection_compares_runtime_bytes_and_profile_binding` keeps covering the runtime
  path with its own fixture. The digest table in its test gains `draft-building-v1`, then stops growing.
- **Owns.** `scripts/qualify-linear.py`, `tests/test_linear_qualification.py`.
- **Depends on.** Green CI's scout list.
- **Required scenarios.** Linear offline. **Sensors.** `tests/test_linear_qualification.py`.

### Unit: Runtime and release command

- **Outcome.** `product-runtime/` and `scripts/cut-release.py` exist as the contract decisions say. The command builds
  and verifies the core, linear, bootstrap, run-delivery and jira products, runs the core, provider, bootstrap and Linear
  qualifiers against those exact archives, and leaves no publishable set on any failure, including over an earlier
  successful run. Workflow-only records are not produced: no one ships a workflow-only archive.
- **Owns.** `product-runtime/`, `scripts/cut-release.py`, `tests/test_cut_release.py`, `tests/test_run_delivery.py` and
  `tests/test_release_entry_budget.py` for their runtime source.
- **Depends on.** Offline Linear qualification, whose command line a successful run calls.
- **Required scenarios.** Build, Runtime seed, Runtime tamper, Version, Failure is closed, No repository writes, Outputs.
  **Sensors.** `tests/test_cut_release.py`, `tests/test_run_delivery.py`, `tests/test_release_entry_budget.py`, and a real
  `cut-release` run.

### Unit: Current-set tests and the pending marker

- **Outcome.** No test compares against a current checked-in record, and the marker is gone. With no current records in
  the repository, nothing can be stale. `validate-registry.py` no longer accepts `--target-base`. The top-level
  `draft-building-v1` qualification records are pinned by digest, and that table then stops growing.
- **Owns.** `tests/test_qualification.py`, `tests/test_provider_qualification.py`,
  `tests/test_bootstrap_qualification.py`, `tests/test_orchestration_state.py` for its guard inventory,
  `tests/test_historical_qualifications.py`, `tests/test_qualification_pending.py`,
  `tests/qualification_pending_support.py`, `scripts/qualification_pending.py`, `scripts/validate-registry.py` and
  `scripts/build-release.py` for their marker handling.
- **Depends on.** Runtime and release command, whose `product-runtime/` and rebinding its replays use.
- **Required scenarios.** Qualifier portability, History. **Sensors.** `tests/test_qualification.py`,
  `tests/test_provider_qualification.py`, `tests/test_bootstrap_qualification.py`, `tests/test_orchestration_state.py`,
  `tests/test_historical_qualifications.py`, `scripts/validate-registry.py`.

### Unit: Tag-driven publish

- **Outcome.** `release.yml` triggers on version tags only. It keeps the guards that the tag is annotated and merged to
  the default branch, and that an existing release is never replaced, adds the ordering guard, runs the full matrix as a
  required job, then `cut-release`, then provenance over every output file, then publishes every output file. `ci.yml`
  becomes callable and gains a `cut-release` job, so every change request proves the release path. Notes are the tag's
  annotation, when it has one, then the digest table, then GitHub's generated list of merged change requests.
- **Owns.** `.github/workflows/release.yml`, `.github/workflows/ci.yml` after the Green CI unit merges,
  `scripts/release_channel.py`, which holds the ordering guard and notes rendering as functions with a command line
  `release.yml` calls, and `tests/test_release_channel.py`.
- **Depends on.** Green CI, and the runtime and release command unit.
- **Required scenarios.** Channel, Notes. **Sensors.** `tests/test_release_channel.py` and its change request's CI.

### Unit: Lanes always run as sessions

- **Outcome.** The operator's ruling is standing text: a lane is always a session of its own, never a subagent and never
  the lead working in its own session. When a host cannot start or message sessions, no lane starts; the lead records
  the blocker and brings it to the operator, who may choose to deliver the tickets one at a time through `$execute-work`.
  That case joins the list of what the operator is brought in for, in the skill and the guide. The lead no longer takes
  tickets itself. Subagents remain how a lane realizes its review lenses. The catalog's statement of the old fallback,
  including keeping undelivered messages in the lead's notes as part of it, is rewritten to match.
- **Owns.** `skills/orchestrate-work/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`, the
  `orchestrate-work` entry in `catalog.json`, `provenance.lock.json`, and `tests/test_lane_ruling_corpus.py`, a new file
  holding the ruling's pins and the absence of the old fallback's words.
- **Depends on.** Offline Linear qualification and current-set tests, which remove the drift tests that would turn a skill
  change red.
- **Required scenarios.** Lane ruling. **Sensors.** `tests/test_lane_ruling_corpus.py`,
  `scripts/update-provenance.py --check`, `scripts/validate-registry.py`, `tests/test_registry.py`.

### Unit: Standing docs

- **Outcome.** `README.md`, `AGENTS.md`, `qualifications/product/README.md` and `docs/product-generation.md` describe
  this flow. The README's install instructions download the latest release without naming it, verify provenance pinned
  to the release workflow as signer, and say that releases before the first version tag carry no attestation. A short
  on-demand Linear connector check document exists, for when the connector or Linear-facing skill text changes; it gates
  nothing and retains no attestation. A candidate's promotion is described, including that the final's digests differ
  from the candidate's because its release id does. The docs say a local suite run can leave bytecode that makes a local
  `cut-release` refuse, and how to clear it. The README's install section warns, without naming a set, that
  releases before the first version tag, which is what latest resolves to until then, carry a 0.7.7 runtime without the
  installed-data protocol, so an adopter whose installed runtime has `installed-data-proof/1` must not apply them.
  `docs/product-generation.md` names `draft-building-v1` and earlier sets as those releases.
- **Owns.** Those documents, `docs/linear-connector-check.md`, `tests/test_readme.py`,
  `tests/test_declared_mechanics_corpus.py`, and `tests/test_no_current_set_names.py`, the set-name scan.
- **Depends on.** Every other unit.
- **Required scenarios.** README, Set names. **Sensors.** `tests/test_readme.py`, `tests/test_declared_mechanics_corpus.py`,
  `tests/test_no_current_set_names.py`.

## Standing rules this delivery sets

- **History frozen in place.** `draft-building-v1` is the last set in git. Every checked-in archive, record and
  attestation keeps its bytes and its pin. Older tags remain, and no longer match the publish trigger.
- **Riskier releases.** A runtime or record-shape change goes out as a candidate, is applied in one adopter, and is then
  promoted by tagging the final version on the same commit.
- **Immutable releases.** The repository setting is turned on after merge, under the authority the owner gave this
  delivery.

## Out of scope

- Any change to lifecycle, verification, manifest format or what an adopter's `apply` accepts. `build_release` and
  `verify_release` keep their refusals.
- Any new profile, host or adapter. No skill text changes except the lane ruling.
- An orchestration binding for this repository.
- Rewriting git history or removing checked-in archives.
- Cutting a release. The first tag is a later decision for the owner.
- Scheduling the connector check: a live check needs a host with the connector, which Actions does not have. It is
  recorded as a follow-up item.
- Branch protection or tag rulesets.

## Finish line

- The integration change is merged to the default branch, and its change request's CI and the default branch's CI on
  the merged commit, including the `cut-release` job, passed on all three operating systems, with the run links reported.
- A `cut-release` run from the merged tree produces five verified products and four passing records, and leaves the
  repository unchanged.
- Immutable releases are on, or the reason they are not is reported.
- The epic and its items are closed with this evidence, every lane checkout is cleaned up, and any deferral has its
  follow-up item.

## Dependencies

1. Green CI starts first and reports its scout list before anything else starts.
2. Offline Linear qualification follows that list.
3. Runtime and release command follows offline Linear qualification.
4. Current-set tests and tag-driven publish follow runtime and release command; publish also follows Green CI.
5. The lane ruling follows offline Linear qualification and current-set tests.
6. Standing docs come last.
7. The integration change request follows all of them, and is where the whole suite must pass on all three systems.

## Test scenarios

- **CI.** From a fresh clone and CI's own commands, the validate job's steps pass with no `__pycache__` under `skills/`.
  The matrix's `fail-fast` is off. `ci.yml` names neither `--target-base` nor `TARGET_BASE`, and no test pins the removed trigger or arguments. This holds on
  Ubuntu at this unit and on all three systems at integration, apart from failures named to another unit.
- **Build.** Building twice from one tree gives identical archive digests. Each product verifies, carries every runtime
  file byte for byte, and binds `workflowContentSha256` to its own profile. `product-runtime/` is unchanged after a run,
  and each profile's descriptor differs from the stored one only in that field.
- **Runtime seed.** At seeding, the lane compares `product-runtime/` with this plan's frozen list and with the generation
  named by its full digest, and records that comparison in its hand-off; its testing lens repeats the comparison. A test
  pins the frozen digests of the seeded files and that `installed-data-proof/1` is present in the seeded `cli.mjs`, until a
  reviewed change replaces the seed. The run-delivery suite passes against it. If it does not, the lane stops and the lead
  decides.
- **Runtime tamper.** A runtime file altered without its descriptor refuses to build.
- **Version.** `v1.2.3` and `v1.2.3-rc.1` are accepted. `1.2.3`, `v1.2`, `v01.2.3`, `v1.2.3-beta`, `some-set-v1`
  and an empty string are refused before anything is written.
- **Failure is closed.** When any qualifier fails, the command exits non-zero and the output holds no publishable set,
  including when the output directory held an earlier successful run.
- **No repository writes.** After a successful run `git status` is unchanged.
- **Outputs.** The output holds exactly the named files, each identified `<profile>-<version>`.
- **Linear offline.** The record has exactly the four remaining scenarios and no attestation, collector or receipt
  member. A Linear archive built against a different core, a degraded-outcome gap and a direct transport action in the
  candidate each still fail closed.
- **Qualifier portability.** The core, provider, bootstrap and Linear qualifiers still run from an archive built from the
  tree on every matrix operating system, without comparing against a checked-in record.
- **Channel.** Only version tags trigger publish, and old-style set tags do not. The annotated and merged guards, the
  ordering guard and the no-replace check precede publishing, and a release that already exists stops later jobs. `tests/test_release_channel.py` runs the ordering guard against a temporary repository with real tags, including a first
  version with no earlier version tag and old-style set tags: a lower or equal version, compared with every version tag except the one being evaluated, or a commit behind
  the highest version tag, is refused, a re-run of a failed run for the same tag is not refused by the guard itself, and promoting a candidate on its own commit is accepted. Text pins cover only the workflow's
  wiring. The matrix job is a dependency of the publish job. Provenance precedes publishing, and its subject and the
  upload both cover every output file. A final is explicitly latest; a candidate is a prerelease and explicitly not.
  `--clobber`, `gh release upload` and `gh release delete` stay absent. `release.yml` holds no workflow-level write
  permission, its `uses:` job grants only `contents: read`, the write permissions appear only on the publish job, and its
  install step names the test extra and CI's Python version.
- **Notes.** Notes rendering, run on a signed annotation and an empty one, holds the annotation without any signature
  block and every archive's digest, followed by the generated list, and a candidate's promotion still produces a usable
  list.
- **History.** Every pinned historical digest still matches, including all of `lead-selected-checks-v1` and
  `draft-building-v1`.
- **README.** Every `qualifications/` path it names exists, it still points at history, and it names no current set. It does not
  contain `draft-building-v1`, and its install section holds the warning about releases before the first version tag.
- **Set names.** Outside `tests/test_historical_qualifications.py`, and the historical digest table and pins in
  `tests/test_linear_qualification.py`, no test names a release set, and `tests/test_no_current_set_names.py`, which excludes
  itself, proves it by scanning `tests/` for `draft-building-v1`. A test still reading a historical archive, such as `linear-adoption-v1` in
  `tests/test_product_release.py`, is recorded as reading history.
- **Lane ruling.** `tests/test_lane_ruling_corpus.py` holds the ruling in the skill and the guide and the absence of the old
  fallback's words from both and from the catalog. A mutation proves that test, with the digest refreshed as `AGENTS.md`
  directs.

## Test dispositions

- **Replay against a checked record**: `test_checked_product_core_replays_exactly`,
  `test_checked_product_provider_replays_exactly`, `test_checked_record_replays_exactly`,
  `test_checked_bootstrap_qualification_replays_exactly` and `test_checked_product_linear_record_replays_exactly` change
  to qualify an archive built from the tree, asserting a pass and the record's shape. The byte comparison goes. The core,
  provider and bootstrap replays build products from `product-runtime/` with the same rebinding as `cut-release`.
- **`test_trusted_sensor_change_changes_the_driver_identity`**: the comparison with the checked record goes; the assertion
  that a changed trusted sensor changes the digest stays.
- **`test_current_bootstrap_product_replays_and_matches_source`**: removed, like the workflow-projection test, with its
  qualification pass covered by the built-archive bootstrap test.
- **The inline comparison with the checked core record in `tests/test_qualification.py`**: goes. The first-run versus
  replay comparison in that test stays.
- **`test_checked_product_workflow_projection_matches_current_source`** and the frozen collector-input test: removed. The
  first guarded drift between checked archives and source, which no longer exists.
- **Linear attestation and collector tests**, including the historical protected-record test that calls the removed
  validators: removed. The historical bytes stay pinned by digest. Tests that only carry attestation fixtures
  (`test_cli_failure_preserves_inputs_and_never_reuses_old_success`,
  `test_linear_cannot_inherit_qualification_for_different_shared_core`,
  `test_exact_offline_record_shape_and_tampering_are_checked`) are ported, not removed.
- **`tests/test_qualification_pending.py`, its support module, and every `skip_if_pending` and `pending()` call**, including
  the conditional assertions in the qualification tests: removed.
- **`tests/test_orchestration_state.py`**: `GUARD_CALLS`, `_guard_calls` and `SkipGuardTests` are removed, along with any
  import only they use. The guards they pin no longer exist.
- **Current-set literals used as fixture release ids** in the qualification tests become neutral ids, such as
  `core-qualification-fixture`, or are derived.
- **`RUNTIME_SOURCE` in the run-delivery suite and the entry-budget test's checked archive**: read `product-runtime/` or a
  build from the tree. `test_runtime_requires_exact_workflow_and_own_bytes` stays as written.
- **`tests/test_release_channel.py`**: rewritten to the new trigger and guards, keeping the no-replace pins.
- **`tests/test_declared_mechanics_corpus.py`**: pins for the changed `AGENTS.md` sentences are re-derived, including "named
  exactly its set name" and "re-earned against the candidate's release", and the README pin on the attestation's
  `auditReferences` order goes with the live section it describes. "Never move or reuse a release tag" and "that release
  is the channel adopters download from" stay.
- **`tests/test_readme.py`**: the current-Linear-evidence test goes; the named-paths test stays.
- **`tests/test_historical_qualifications.py` and the digest table in the Linear test**: `draft-building-v1` is added, and
  the tables then stop growing.

## Shared resources

The suite's existing `run_serially` marks stay. `cut-release` uses only its output directory and per-qualifier temporary
roots. `.github/workflows/ci.yml` passes from the Green CI lane to the tag-driven publish lane when the former merges.
`tests/test_product_release.py` reads a historical archive, is unchanged, and belongs to no unit.
`provenance.lock.json` and `catalog.json` belong to the lane ruling unit alone. No two lanes hold one file.

## Sensors

Each unit runs its own tests and declared sensors. The lead runs the checks on the merged head of the delivery base after
each merge, and the integration change request runs `scripts/validate-registry.py`, `tests/test_registry.py`,
`scripts/update-provenance.py --check` and `scripts/run-tests.py`, after bootstrapping as `AGENTS.md` directs, plus a real
`cut-release` run. Workflow syntax has no local executor here, so a change request's own CI is the proving sensor for
`ci.yml`, including its new job. `release.yml` cannot run before a real tag; its risk is carried by the channel tests, by
`cut-release` running in CI under the same environment, and by the candidate path.

## Approval handoffs

The owner granted authority for this delivery end to end, including judgement calls, merging and the immutable-releases
setting. No release is cut. Each lane's delivery review is the two mandated lenses, correctness and testing, under the
default bound of four rounds. The integration change adds an adversarial lens, because it changes what stands behind
published bytes. No delivery product is installed in this repository, so no release binds the review. The repository
declares no run-event command, so nothing is emitted.

## Decisions recorded

- Plan review is unbounded, per `plan-work`.
- Release-note fragments, proposed in discussion, are replaced by the tag's annotation: fragments need a clearing commit,
  which contradicts "no release edits a file".
- Sessions-only means a session-less host now stops rather than degrades. The operator ruled this; the first final's
  annotation says so for adopters.
- A scheduled connector check, proposed in discussion, is on demand for now, as recorded under Out of scope.
- `product-runtime/` is preferred to fetching a prior release's asset, so builds and tests need no network.
- The runtime is seeded from the named Athena generation rather than `draft-building-v1`, to avoid rolling back a protocol
  an adopter uses under an unchanged version label.
- The change is decomposed into lanes on a delivery base at the operator's direction, instead of one change set.
- The accepted risk: a connector break can ship and surface only at an on-demand check or in an adopter's run.
