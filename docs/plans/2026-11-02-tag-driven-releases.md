---
title: "feat: Make a release one annotated tag, with the machine doing the rest"
type: feat
status: approved
date: 2026-11-02
execution_posture: test-first
context: One four-line behaviour change took a 30-file release pull request, hand-edited pins in eight test files and a live Linear run. Several repositories now adopt the corpus, and no adopter reads qualifications from this repository.
---

# feat: Make a release one annotated tag, with the machine doing the rest

## Outcome

The owner, or an agent the owner authorizes, cuts a release by pushing one annotated version tag. From the tagged tree,
the release workflow runs the repository's checks, builds and qualifies the products, and publishes them with their
records and build provenance. If anything fails, nothing is published. No release pull request exists, no release
edits a file in the repository, and no release needs a live tracker run.

## Evidence behind this plan

- **Adopters.** Athena has the `linear` profile installed under its own release id, so the Linear profile stays
  qualified offline; only the live gate goes. Neither Athena nor the harness reads this repository's
  `qualifications/`: their references are to the harness's own records.
- **Release ids.** `release.py` requires only a non-empty string, so the version scheme needs no lifecycle change.
- **Runtime.** A release has been copying the runtime out of the previous archive. The harness builds it, and
  `workflowContentSha256` is already rebound per profile at build time.
- **Repository.** It is public, so build provenance is available. Immutable releases are available and currently off.
- **The live run.** The qualifier checks an attestation's shape, order and bindings; the README already says it does
  not independently prove the external calls happened. A connector also breaks on Linear's or the host's schedule, not
  on this repository's tags.

## In scope

- **Runtime in the tree.** The runtime's files live in one top-level directory, `product-runtime/`, seeded byte for
  byte from the current product. Changing the runtime is an ordinary reviewed pull request. The build rebinds
  `workflowContentSha256` per profile, so the stored value is not authoritative.
- **One release command.** `scripts/cut-release.py` takes a version and an output directory. It builds and verifies
  the core, linear, bootstrap, run-delivery and jira products from the tree with that runtime, runs the core, provider,
  bootstrap and Linear qualifiers against those exact archives, and writes archives, release files and records to the
  output directory. It writes nothing in the repository. Any failure exits non-zero and leaves the output directory
  without a publishable set. Workflow-only records are not produced: no one ships a workflow-only archive.
- **Offline Linear qualification.** `qualify-linear.py` keeps core binding, install and host discovery, controlled
  degraded outcomes and forbidden transport, under a new record schema version. The collector input, the attestation
  and their two live scenarios go, along with their code and tests. Linear's core projection must still match the
  separately qualified core.
- **On-demand connector check.** A short document describes a manual live check of the Linear connector on disposable
  work, for when the connector or Linear-facing skill text changes. It gates nothing and retains no attestation.
- **Tag-driven publish.** `release.yml` triggers on version tags only. It keeps the guards that the tag is annotated
  and merged to `main` and that an existing release is never replaced. It runs the full CI matrix as a required job,
  then `cut-release`, then provenance, then publishes. `ci.yml` becomes callable, and gains a job that runs `cut-release`
  end to end, so every pull request proves the release path works.
- **Release notes.** The tag's annotation carries any adopter-facing notes, followed by GitHub's generated list of
  merged pull requests and the archive digest table. This replaces per-release note files and fragments, which would
  need a clearing commit and so contradict "no release edits a file".
- **Versions.** Tags are `vMAJOR.MINOR.PATCH`, optionally `-rc.N`. Major means an adopter must act, minor is additive,
  patch changes no contract. Release ids are `<profile>-<version>`. A release candidate publishes as a prerelease and
  never as latest. A runtime or record-shape change goes out as a candidate, is applied in one adopter, and is then
  promoted by tagging the final version on the same commit.
- **History frozen in place.** `draft-building-v1` is the last set in git. Every checked-in archive, record and
  attestation keeps its bytes and its pin. Older tags remain, and no longer match the publish trigger.
- **Pending marker removed.** `qualification-pending.json` exists to say committed records are stale. With no current
  records in the repository nothing can be stale, so the marker, its reader, its test support, its `--target-base`
  argument and its skips go.
- **Standing docs.** `README.md`, `AGENTS.md`, `qualifications/product/README.md` and any product doc that describes
  releasing are rewritten to this flow. The README's install instructions download the latest release without naming
  it, and show how to verify provenance.
- **Immutable releases.** The repository setting is turned on after merge, under the authority the owner gave this
  delivery.

## Out of scope

- Any change to lifecycle, verification, manifest format or what an adopter's `apply` accepts.
- Any new profile, host or adapter, and any change to skill text, so no catalog, profile or provenance output changes
  except where a listed output must move.
- Rewriting git history or removing checked-in archives.
- Cutting a release. The first tag is a later decision for the owner.
- Scheduling the connector check: a live check needs a host with the connector, which Actions does not have. It is
  recorded as a follow-up item.
- Branch protection or tag rulesets.

## Finish line

- The change is merged to `main`, and the pull request's and `main`'s CI, including the `cut-release` job, ran on the
  merged content with their results reported.
- A dry run of `cut-release` from the merged tree produces five verified products and four passing qualification
  records, and leaves the repository unchanged.
- Immutable releases are on, or the reason they are not is reported.
- The tracked item is closed with this evidence, and any deferral has its follow-up item.

## Dependencies

1. The runtime directory comes first: the release command and the tests that need a runtime read it.
2. Offline Linear qualification comes before the release command, which calls it.
3. The release command comes before the workflows that call it.
4. Test dispositions land with the unit that makes each test stale; docs land last.

All of it lands as one change set: the checked-record replays, the Linear scenarios and the marker are entangled, so a
partial merge would leave `main` red or describing a flow that does not exist.

## Test scenarios

- **Build.** Building twice from one tree gives identical archive digests. Each product verifies, carries every
  runtime file byte for byte, and binds `workflowContentSha256` to its own profile.
- **Runtime tamper.** A runtime file altered without its descriptor refuses to build.
- **Version.** `v1.2.3` and `v1.2.3-rc.1` are accepted. `1.2.3`, `v1.2`, `v1.2.3-beta`, `draft-building-v1` and an
  empty string are refused before anything is written.
- **Failure is closed.** When any qualifier fails, the command exits non-zero and the output holds no publishable set,
  including when the output directory held an earlier successful run.
- **No repository writes.** After a successful run `git status` is unchanged.
- **Release ids.** Every archive and release file is named and identified `<profile>-<version>`.
- **Linear offline.** The record has exactly the four remaining scenarios and no attestation, collector or receipt
  member. A Linear archive built against a different core, a degraded-outcome gap and a direct transport action in the
  candidate each still fail closed.
- **Qualifier portability.** The core, provider, bootstrap and Linear qualifiers still run from an archive built from
  the tree on every matrix operating system, without comparing against a checked-in record.
- **Channel.** Only version tags trigger publish, and old-style set tags do not. The annotated and merged guards and
  the no-replace check precede publishing. The matrix job is a dependency of the publish job. Provenance precedes
  publishing. A candidate is a prerelease and not latest. `--clobber`, `gh release upload` and `gh release delete`
  stay absent.
- **Notes.** The rendered notes hold the tag's annotation, when it has one, and the digest of every archive.
- **History.** Every pinned historical digest still matches, including all of `lead-selected-checks-v1` and
  `draft-building-v1`.
- **README.** Every `qualifications/` path it names exists, and it names no current set.

## Test dispositions

- **Replay against a checked record** in the core, provider, bootstrap and Linear qualification tests: changed to
  qualify an archive built from the tree, asserting a pass and the record's shape. The byte comparison is what goes.
- **`test_checked_product_workflow_projection_matches_current_source`** and the frozen collector-input test: removed.
  The first guarded drift between checked archives and source, which no longer exists.
- **Linear attestation and collector tests**, including the historical protected-record test that calls the removed
  validators: removed. The historical bytes stay pinned by digest.
- **`tests/test_qualification_pending.py`, its support module and every `skip_if_pending`**: removed.
- **`RUNTIME_SOURCE` in the run-delivery suite and the entry-budget test's checked archive**: read `product-runtime/`
  or a build from the tree.
- **`tests/test_release_channel.py`**: rewritten to the new trigger and guards, keeping the no-replace pins.
- **`tests/test_declared_mechanics_corpus.py`**: pins for the changed `AGENTS.md` sentences are re-derived, including
  "named exactly its set name" and "re-earned against the candidate's release". "Never move or reuse a release tag" and
  "that release is the channel adopters download from" stay.
- **`tests/test_readme.py`**: the current-Linear-evidence test goes; the named-paths test stays.
- **`tests/test_historical_qualifications.py` and the digest table in the Linear test**: `draft-building-v1` is added
  and the tables then stop growing.

## Shared resources

The suite's existing `run_serially` marks stay. `cut-release` uses only its output directory and per-qualifier
temporary roots, so it needs no serialization. The release workflow alone holds the `contents`, `id-token` and
`attestations` write permissions; `ci.yml` needs none of them, and a called workflow's permissions must not widen them
for the `cut-release` check job.

## Sensors

`scripts/validate-registry.py`, `tests/test_registry.py`, `scripts/update-provenance.py --check`, and
`scripts/run-tests.py`, after bootstrapping the worktree's test environment as `AGENTS.md` directs, plus a real
`cut-release` run. Workflow syntax has no local executor here, so the pull request's own CI is the proving sensor for
`ci.yml`, including its new job. `release.yml` cannot run before a real tag; its risk is carried by the channel tests,
by `cut-release` running in CI, and by the release candidate path.

## Approval handoffs

The owner granted authority for this delivery end to end, including judgement calls and merging. No release is cut.
Delivery review is two mandated lenses, correctness and testing, plus adversarial because this changes what stands
behind published bytes, under the default bound of four rounds. No delivery product is installed in this repository,
so no release binds the review. The repository declares no run-event command, so nothing is emitted.

## Decisions recorded

- Plan review is unbounded, per `plan-work`.
- Release-note fragments, proposed in discussion, are replaced by the tag's annotation, as recorded under Release notes.
- A scheduled connector check, proposed in discussion, is on demand for now, as recorded under Out of scope.
- `product-runtime/` is preferred to fetching a prior release's asset, so builds and tests need no network.
- The accepted risk: a connector break can ship and surface only at an on-demand check or in an adopter's run.
