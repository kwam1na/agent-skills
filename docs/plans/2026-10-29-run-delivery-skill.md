---
title: "feat: Add run-delivery, the commands that carry out an orchestrated delivery, with a setup check and an adoption guide"
type: feat
status: approved
date: 2026-10-29
execution_posture: characterization-first
context: The first adopter delivered a 20-story epic with orchestrate-work, obtain-review and delivery-views, driven by a command-line tool that lived outside the corpus on one engineer's machine. Another engineer could not adopt the workflow without that machine and that engineer.
---

# feat: Add run-delivery, the commands that carry out an orchestrated delivery, with a setup check and an adoption guide

## Outcome

An engineer who has never run an orchestrated delivery can adopt one in their own POSIX repository from a released
corpus:

- They install a release, write a small configuration, and run one **setup check**. It names every missing
  prerequisite and how to fix it.
- They follow one **adoption guide** from installation to a closed delivery, as lead or as lane.
- Every mechanical step of a delivery runs as a **command** shipped in the corpus:
  - briefs, round files and gates;
  - hand-offs and acceptance;
  - evidence, ship and close-out.

  In-app checks run through the same commands, but against the adopter's own build, serve and check adapters. The
  corpus ships their contract, a fixture page and a contract test, not an adapter for any particular application.

The commands ship as a new skill, `run-delivery`, in a new profile, `run-delivery`, layered on `delivery-views`. The
skill wraps what the release already carries and replaces none of it:
- the `orchestrate-work` state tool and its round validator;
- the `obtain-review` round-brief template;
- the lens charters.

A fixture adopter in the test suite takes an empty repository through the guide's own example blocks to a clean setup
check and a closed delivery. It runs from an installed release and has no knowledge of the first adopter.

## Current-State Evidence

**The tool.** The first adopter's delivery ran on a standard-library Python tool of 2,823 lines. Besides the main
tool, it had:
- two forge adapters;
- four templates;
- a fixture page;
- a 17-case self-test;
- a configuration reference.

It lived in an uncommitted folder on the lead's machine, linked into that engineer's personal skills folder. Its
commands:

| Role | Commands |
| --- | --- |
| Lane | `review-prepare`, `review-reduce`, `gate`, `build`, `app-check`, `app-video`, `push`, `open-pr`, `pr-review`, `handoff` |
| Lead | `lead-setup`, `lead-init`, `lead-brief`, `lead-assign`, `lead-accept`, `lead-reconcile`, `lead-evidence`, `lead-cleanup`, `lead-close`, `ship` |
| Setup | `config`, `adapter-test` |

It already followed the corpus's portability rule: repository and delivery specifics live in configuration and argv
adapters. It reads, in order, then its own file for whatever none of them declare:
1. the orchestrate-work binding;
2. the delivery-views configuration;
3. the harness configuration.

**What adopting it took.** Listing what a second engineer would need surfaced these gaps:

1. **No distribution.** The tool had no commits and no remote, and the release it wrapped was built from a personal
   checkout.
2. **Setup by hand.** All of the following existed only on the delivery branch:
   - the binding;
   - the views configuration;
   - the harness configuration;
   - the tool configuration;
   - the gate scripts;
   - the merge script.

   Each was written by hand, with no check that the set was complete or consistent until a command failed
   mid-delivery.
3. **Hidden prerequisites:**
   - forge and tracker tokens in named environment variables;
   - ffmpeg and ffprobe, for video evidence;
   - Node 22.6 or newer, to read the harness configuration;
   - a headless browser inside the adopter's in-app adapter;
   - a writable lease lock and baseline build folder;
   - a POSIX host.

   Each surfaced only as a failure.
4. **Knowledge held in one engineer's agent memory**, invisible to anyone else's agent:
   - lanes run as their own sessions the operator can open and steer, not as hidden background agents;
   - lanes never merge the base, and read the delivery's settings from the base on the remote;
   - review evidence must be captured for the line map to show rounds;
   - after the tool is upgraded, a running reconcile sweep keeps the old code until it is restarted;
   - the evidence page routine;
   - sign-in values stay out of command arguments.
5. **Host-specific content:**
   - the skill text named one host's session and publishing tools;
   - a lane checkout default sat in one host's folder;
   - a review command told lanes to use one host's messaging tool;
   - the evidence page carried one host's meta tag.
6. **No runbook.** The order of operations was spread across three places: the orchestration guide, the tool's skill
   text and that memory.

**Defects the delivery found in the tool, fixed before this plan:**
- `lead-close` left the reconcile sweep running.
- `ship` staged the product branch but could not run the gates there, because the gate scripts live with the
  delivery's tooling and never ship.

**What adding a profile costs** (the `delivery-views` precedent):
- **Code and schemas:** the supported-profile lists in the release and receipt code, and the profile enums in two
  schemas, must change.
- **Archives:** those files ship inside every profile's archive, so every profile's bytes change.
- **Qualification:** all four qualification sets go stale, and must be re-earned under a new set name. Linear needs a
  live host attestation.
- **Runtime:** a product release needs its runtime rebound to the new profile's workflow projection.

## Amendment 1 (2026-10-30): the plan chapter

- **Decision.** The operator approved `2026-10-30-plan-chapter.md` with its C12-A. Its units P1–P5 join this delivery
  before U5, so the one qualification pass in U5 covers both.
- **What changes here:**
  - Out of scope no longer excludes the plan chapter's `delivery-views` changes.
  - P1–P5 are added under Implementation Units, copied from that plan. Its Contract Decisions C1–C12 bind them.
  - U5 depends on P1–P5, and its ownership, scenarios and finish line widen to cover them.
- **Review.** The plan-chapter plan was reviewed to alignment in 4 rounds. This amendment's own review and the
  operator's approval are recorded under Plan Review Status.

## Amendment 2 (2026-10-30): sync with the default branch, and the Linear attestation handed off

- **Decision.** No host with the Linear MCP is available to the lead. The operator chose to finish everything else,
  open a draft change request into the default branch, and have an agent on a host with the Linear MCP collect the
  attestation and complete it there. The qualification guard is not loosened.
- **Why a new unit.** The default branch moved after U5 merged: it cut a release that re-earned every set and changed
  `delivery-views` and `orchestrate-work`. Every archive changes with it, so U5's records and the frozen Linear
  collector input are stale.
- **What changes here:**
  - U6 is added under Implementation Units. It follows U5.
  - The release finish line is met on the completing host, after its attestation; the marker names only `linear`
    until then.
- **Review.** This amendment adds no contract decision; U6's own review rounds cover its change.

## Execution Posture

- **`characterization-first`:** moving the tool in (U1). The 17 self-test cases become the corpus test. Each must pass,
  or skip with a named reason, against the moved tool before anything is renamed or re-pathed.
- **`test-first`:** every behaviour change in U2, and the setup check in U3.
- **`sensor-only`:** the adoption guide and skill text (U4).
- **U5** changes production lifecycle code paths only through the registry, so it runs the full qualification
  sensors.
- **The plan chapter (P1–P5):**
  - `test-first` for P1–P4;
  - `characterization-first` for the existing line-map page tests and goldens;
  - `sensor-only` for P5.

## Plan Review Status

The outcome-correctness and adversarial lenses aligned unanimously after 5 rounds, on 2026-10-29. The evidence is in
`2026-10-29-run-delivery-skill.review.json`. There is no round bound. Approved by the operator on 2026-10-29.

| Round | What happened |
| --- | --- |
| 1 | 43 findings across both lenses. |
| 2 | All resolved; 14 new findings, one of them P1 from both lenses (the brief's release identity). |
| 3 | All resolved; one P3 from each lens. |
| 4 | Outcome-correctness approved; one P3 from adversarial. |
| 5 | Both approved, with no findings. | Plan review continues until the lenses align, with no round bound, per `plan-work`.

**Amendment 1** was reviewed by the outcome-correctness and adversarial lenses, which aligned in 3 rounds on
2026-10-30. Two non-blocking wording fixes were adopted as written. The evidence is in
`2026-10-29-run-delivery-skill.amendment-1.review.json`. The operator approved folding the plan chapter in (its C12-A)
on 2026-10-30.

## Scope

### In scope

1. **The `run-delivery` skill:** `SKILL.md`, the command tool, the reference forge adapters, the templates, the
   adapter fixture page and the configuration reference, moved from the first adopter's tool.
2. **Registry, lifecycle and qualification for a fifth profile:**
   - the supported-profile lists and schema enums;
   - the catalog, profile and provenance entries;
   - a validation-inventory scenario;
   - fresh records for core, bootstrap, provider and linear;
   - a runtime for the new projection;
   - one release.
3. **Release-relative operation:** the tool finds its sibling skills and its release identity from its own installed
   generation.
4. **Host-neutral content:** host behaviour is described with a degraded path, in every shipped file and in command
   output.
5. **The setup check,** `doctor`.
6. **The adoption guide,** `references/adopting.md`.
7. **Tests:** the self-test ported into the corpus suite, the setup check's tests, a fixture adopter run from an
   installed release, and leakage and host-term scans.
8. **CI:** installing ffmpeg and Node 22.6 on the POSIX runners, so the video and harness cases run there.

### Out of scope

- Changes to `orchestrate-work`, `obtain-review` or `review-work` behaviour or schemas.
- Changes to `delivery-views` other than the plan chapter (Amendment 1).
- The first adopter's own changes, which follow as a separate change in that repository:
  - moving its configuration to its default branch;
  - its in-app login adapter.
- A tracker-mutation capability. Lanes move tickets with the host's tracker tools, or report the move for the lead
  to make.
- New hosts, or a session-messaging abstraction.
- Windows support. The tool is POSIX-only, and says so.
- A command that writes an adopter's configuration. The guide gives the minimal example and `doctor` checks it.
- Adding `run-delivery` to the `linear` profile.

## Contract Decisions

**C1. Names.**

New names:
- the skill and profile: `run-delivery`;
- the tool: `skills/run-delivery/scripts/run_delivery.py`;
- the configuration file: `.agents/run-delivery.json`.

Every old identifier gets one of two treatments:

| Identifier | Decision |
| --- | --- |
| `.agents/flow.json` | Read when `.agents/run-delivery.json` is absent. `doctor` reports `wrong` with the rename. |
| `DELIVERY_FLOW_CONFIG`, `DELIVERY_FLOW_DELIVERY`, `DELIVERY_FLOW_LOCAL` | Renamed to `RUN_DELIVERY_*`. The old variable is read when the new one is unset, and `doctor` reports it. |
| Archive default `~/.local/share/delivery-flow/archive/...` | Renamed to `.../run-delivery/archive/...`. Old archives are not moved. |
| Schema values `delivery-flow/1`, `delivery-flow-delivery/1` | Kept as frozen contract names, so existing files stay valid. |
| `FLOW_FORGE_REPOSITORY`, `FLOW_FORGE_TOKEN_ENV` | Kept: they are the contract custom forge adapters receive. |
| Build stamp `.flow-build.json` | Kept: existing baseline builds and serve adapters depend on it. |
| Processes named `flow.py` | Matched alongside `run_delivery.py` by `lead-close` and the command-path detection. |

- The fallbacks are listed in one pinned test. Removing any of them takes a later approved unit that changes that test.

**C2. Profile.**
- A new `run-delivery` profile includes `delivery-views`.
- `linear` does not include it.
- The catalog entry's dependencies are `orchestrate-work`, `delivery-views` and `obtain-review`, all required.
- Adding the profile follows the `delivery-views` precedent in full:
  - the supported-profile lists in `agent_skills/release.py` and `agent_skills/receipt.py`;
  - the profile enums in `schemas/receipt.schema.json` and `schemas/release-manifest.schema.json`;
  - a `qualification-pending.json` marker for all four sets, from U1 until U5 deletes it;
  - re-earning core, bootstrap, provider and linear under the set name `run-delivery-v1`, with retained records
    keeping their bytes;
  - the linear live host attestation, collected by the lead in a real host session;
  - the product archives described next.
- **Products.**
  - As in the precedent, the re-earned sets carry bootstrap, core and linear product archives under
    `qualifications/product/`, named `*-run-delivery-v1`. Each has its own runtime descriptor matching its own
    workflow projection.
  - New is a `run-delivery` product archive, the one adopters install. Its runtime is the same bundle the current core
    product carries, `delivery-runtime/1` at runtime 0.7.4 with Node 22.6.0 as the minimum. Its descriptor's
    `workflowContentSha256` is rebound to the `run-delivery` projection. That rebinding is what "rebound" means here.
  - No new runtime build is needed. If the producer releases a newer runtime before U5, adopting it is a separate
    decision, not part of this plan.
- The profile has no qualifier of its own, as `delivery-views` has none. It is proven three ways:
  - a validation-inventory scenario, `run-delivery-profile-selects-run-delivery`;
  - the fixture-adopter test, replayed against the built release;
  - release verify.

**C3. Locating the release.**
- **Root.** The tool resolves its own path (`realpath`, which follows the host projection links). The generation root
  is three folders above `scripts/`. `orchestrate-work`, `obtain-review` and the lens charters are found there.
- **Identity.** The binding tuple in every brief comes from the repository's receipt, `.agent-skills/active.json`.
  - The running generation's folder name is its archive digest.
  - The tool picks the receipt entry whose `generation` matches that name. That is either the receipt's top-level
    (current) `generation` and `release`, or its `previous` entry.
  - It uses that entry's `release` object: `releaseId`, `profile`, `archiveSha256` and `metadataSha256`.
  - It never takes the active entry for a generation that isn't the one running.
  - If neither entry matches, every brief-writing command refuses with `doctor`'s `wrong` message rather than write a
    partial tuple.
- **Mismatch check.** `doctor` reports `wrong` when the running generation is not the receipt's top-level (current) `generation`.
- **Override.** The configuration's `release` key overrides the root. `doctor` then compares the overridden
  generation's manifest with the receipt in the same way.
- **Path in briefs.** Briefs name the tool by `.agent-skills/current/skills/run-delivery/scripts/run_delivery.py`.
  That path is stable and host-neutral; neither a generation path nor a projection path appears.
  - A lane resolves that path in its own checkout of the delivery base. So the installed generation must be committed
    on the delivery base, as the first adopter's is.
  - `doctor` checks this with `git ls-tree` on `<remote>/<base>`. The fix line is to commit the install.

**C4. Host neutrality.** These are host behaviour, each with the path a host without them takes:

| Host behaviour | Without it |
| --- | --- |
| Lanes as separate sessions the operator can open and steer | Background agents, with each lane's report relayed by the lead |
| Messaging between sessions | The operator carries the lead's message to the lane |
| Publishing the evidence page | A local page the operator shares |
| Tracker moves | The lane reports the move in its hand-off, and the lead makes it |

- No command calls a host API.
- No shipped file or command output names a host tool, except inside an example block the guide marks as one.
- The default lane checkout is `.orchestration/lanes/{key}`, beside the binding's state. `doctor` checks that it is
  ignored by git.
- An explicit `laneCheckout` keeps working.

**C5. Forge adapters.**
- The Bitbucket and GitHub adapters ship as reference adapters behind the forge contract: `find`, `open`, `get`, and
  `reviews` where the forge has it.
- Their only write is opening a change request as a draft, which `open-pr` does only under the delivery's
  `authority` grant.
- This makes `run-delivery` the corpus's first writing forge client. Approving this plan approves that.
- **Tokens:**
  - The Bitbucket adapter reads its token from the variable the configuration names.
  - The GitHub adapter uses `gh` and its own sign-in.
  - Neither prints a token or an Authorization value, in output or in errors. A test covers this.
- **GitHub reviews:** the adapter has no `reviews` command, so `pr-review` reports "unsupported by this forge
  adapter" rather than failing.
  - `doctor` reports `wrong` when `review.pr` is configured with an adapter that has no `reviews` command. Otherwise
    hand-offs would wait on a review that never comes.
- An adopter may declare any other command with the same contract.

**C6. External tools and platform.**
- **Platform.** POSIX only: the tool uses `fcntl`, process groups and `sh`.
  - On Windows, importing it prints one message saying so and exits with code 2.
  - The test module skips there with that reason.
  - The catalog's degraded behaviour says the same.
- **Catalog identifiers.** The entry's `externalTools` lists `git`, `python3`, `ps`, `lsof`, `ffmpeg`, `ffprobe`, `node`
  and `gh`.
- **Versions and conditions** are not in the catalog. The skill text and `doctor` carry them:
  - git 2.31 or newer;
  - Python 3.11 or newer;
  - ffmpeg and ffprobe only for video evidence;
  - Node 22.6 or newer, plus a runtime loader in the generation, only when a harness configuration exists;
  - `gh` only for the GitHub adapter;
  - the adopter's in-app adapters only when in-app checks are declared.

**C7. The setup check.**

Usage:
`run_delivery.py doctor [--delivery <key>] [--offline] [--run-adapters] [--run-gates]`

**Output.** One line per check, with a fix for every line that isn't `ok`:
- `ok`;
- `missing` or `wrong`;
- `skipped`, which names the check it depends on or the flag that enables it.

A check whose prerequisite failed reports `skipped`, not a second failure. Removing one prerequisite therefore yields
exactly one line that is neither `ok` nor `skipped`.

**Exit codes:**
- 0: every required check is `ok`;
- 1: any is not;
- 2: a usage error, or a non-POSIX host.

**What it may change.** By default it writes no tracked file, delivery file or record file. It starts nothing that
outlives it.
- Resolving the configuration fetches the base from the remote. With `--offline` it skips the fetch and reads the
  local `<remote>/<base>`.
- It runs Node on the harness configuration when one exists.
- The guide says both.
- The writability checks create and remove one temporary file.

| Check | Required when |
| --- | --- |
| Host is POSIX; git, Python, `ps` and `lsof` meet C6 | always |
| The running generation matches the repository's receipt (C3) | always |
| The installed generation is committed on the delivery base, so lanes resolve the tool path (C3) | always |
| `review.pr` is not configured with an adapter that lacks `reviews` (C5) | `review.pr` is configured |
| The binding validates against its schema | always |
| Configuration resolves with no conflicts; no old names in use (C1) | always |
| Gates are declared, or the configuration says there are none; each gate and prepare executable resolves | always |
| The lane checkout folder is ignored by git | always |
| The tracker issue URL is set | always |
| The forge adapter answers `find` | a forge is configured, and not `--offline` |
| The forge's write access | reported as "not verified": `doctor` never writes to a forge |
| The tracker snapshot's token variable is set | the binding declares a tracker snapshot |
| `gh` is installed and signed in | the GitHub adapter is configured |
| The in-app build, serve and check executables resolve; the lease and baseline folders are writable | in-app checks are declared |
| The in-app adapters keep their contract, by the `adapter-test` fixture run | `--run-adapters`; takes the lease with no wait, and reports `skipped (lease held by …)` if busy |
| ffmpeg and ffprobe are present, with the filters video evidence uses | any story is marked for video evidence |
| Node 22.6+ reads the harness configuration, and the generation carries the runtime loader | a harness configuration exists |
| A delivery file exists, every story has a lane, and lanes own disjoint files | `--delivery` is given |
| The gates pass on the base, in a fresh prepared checkout that is removed afterwards | `--run-gates` |

**C8. The adoption guide** is the single entry point for a new adopter, linked from `SKILL.md`. Its sections:
1. **What the workflow does,** and who the lead and the lanes are.
2. **Prerequisites,** matching C6.
3. **Install a release.**
4. **Configure the repository:**
   - what each file is for;
   - a minimal example of each, in marked example blocks;
   - the in-app adapter contract, with an outline adapter and the `adapter-test` proof.
5. **Run `doctor`** until it is clean, and read its output.
6. **A first delivery walkthrough,** as marked command blocks:
   - lead setup, init, brief and assign;
   - a lane's loop: review, gates, in-app checks, push, draft change request and hand-off;
   - acceptance, merge, cleanup, evidence, ship and close-out.
7. **Host notes,** matching C4.
8. **Operating rules,** carrying Current-State item 4. These are pinned.
9. **Troubleshooting,** keyed by the messages the commands and `doctor` print.

**Path conventions in the guide:**
- The skill's own files are named by their full installed path.
- Adopter paths appear only inside the marked blocks, in a form the corpus's reference validator does not resolve.
- The registry validation sensor proves both.

**C9. Provenance.** The skill's entry follows the `delivery-views` entry's shape:
- `source.kind: independent-origin`;
- `modification.status: independently-authored`;
- `ownerApproval`, citing the owner's approval to contribute, given on 2026-10-29;
- the corpus license.

New outputs are hand-added to the lock, and then the provenance writer computes their digests.

**C10. Secrets in evidence.** Records, logs, archives and published evidence pages must not carry sign-in values:
- The guide's pinned operating rules say sign-in values come only from the environment or the adopter's adapter.
- `app-check` refuses an adapter argument whose flag name or assignment key matches this pattern, case-insensitive,
  naming the rule:

  ```
  password|passwd|token|secret|api[-_]?key|credential
  ```

  Other arguments containing `key`, such as `--sort-key`, are accepted.
- The guide says evidence pages show application content and should be published privately.

## Implementation Units

### U1. Registry skeleton, and the tool moved in, characterized

**Goal:** The corpus stays valid from the first commit, and the moved tool behaves exactly as it did.

**Ownership:**
- the `qualification-pending.json` marker;
- the supported-profile lists in `agent_skills/release.py` and `agent_skills/receipt.py`;
- the two schema enums;
- `profiles/run-delivery.json`;
- the catalog entry and the provenance entry (C9);
- the `validation-inventory.json` scenario and `expectedScenarioCount`;
- the test pins that list profiles: `tests/test_profile_inventory.py`, `tests/test_release_entry_budget.py`,
  `tests/test_release_integration.py`, and any others the search finds;
- `skills/run-delivery/**`, copied without `__pycache__`;
- the fixture page at `skills/run-delivery/assets/adapter-fixture/`;
- `tests/test_run_delivery.py` and its fake adapters;
- the CI install of ffmpeg and Node 22.6 on POSIX runners.

**Approach:**
1. Search the tests for literal profile lists and counts that the new profile makes stale, and update each one.
2. Copy the tool. Make three changes that alter no behaviour the self-test observes:
   - install the signal handlers in `main()`, not at import;
   - set `sys.dont_write_bytecode` before importing release modules;
   - point `adapter-test` at the shipped fixture page.
3. Port the 17 cases. Each runs the tool as a subprocess with `-B`, not in-process, so the test runner's workers
   keep their own signal handlers, working directory and module state.
4. Mark the module `RUN_SERIALLY = True`. The reason is that cases share a lease lock and environment variables
   within one fixture repository. A fixed port is not the reason.
5. The fixture gives its release a runtime with the same descriptor-only rebind C2 describes:
   1. It extracts the `runtime/` files from `qualifications/product/core-delivery-views-v1.zip`, the current
      qualification set's core product, whose runtime is the one C2 names. The test asserts `runtimeVersion` is
      0.7.4.
   2. It rewrites only `runtime.json`'s `workflowContentSha256` to the digest of the `run-delivery` projection it is
      building.
   3. It passes that folder to the release build as `runtime_directory`.

   The built archive passes release verify, and the installed generation stays clean. The harness case therefore runs
   wherever Node 22.6 exists, CI included, from U1 on, without waiting for U5's product. The video and harness cases
   skip with a named reason only where ffmpeg or Node 22.6 is missing.
6. The module skips on Windows (C6).
7. `setUpClass` builds the `run-delivery` profile from the source tree.

**Required scenarios:**
- Registry validation passes with the marker present.
- The full suite passes, with only the marker's skips and the named tool skips.
- Each of the 17 cases passes against the moved tool, or skips with its named reason.
- No `__pycache__` appears under `skills/` after the suite runs.
- The fixture's built archive passes release verify, with `runtime/` present.

### U2. Release-relative paths, names and host-neutral content

**Goal:** The tool runs from any installed generation under its new names, with no host or personal term in anything
it ships or prints.

**Posture:** `test-first`.

**Ownership:** the tool's configuration, path and process code; the templates; the configuration reference; the
round-brief section pin.

**Approach:**
- C1, C3 and C4 in code and templates.
- The process patterns match both tool names.
- `review-prepare` names no host messaging tool.
- The evidence page template drops the host meta tag.

**Required scenarios:**
- The tool runs through each host projection link and through `.agent-skills/current`. Each brief carries the running
  generation's tuple, read from the matching receipt entry.
- With an older generation still running after an update, briefs carry the receipt's `previous` tuple, and `doctor`
  reports the mismatch.
- When the running generation is in neither receipt entry, `review-prepare` refuses with `doctor`'s message.
- `.agents/flow.json` and the `DELIVERY_FLOW_*` variables are read, and `doctor` reports each.
- `lead-close` stops these when started under either tool name:
  - a `lead-reconcile --every` sweep;
  - the merge queue;
  - the renderer.

  A process scan confirms each is gone.
- `app-check` refuses `--password=x` and `--api-key x`, and accepts `--sort-key x`.
- The forge adapters' only write is a draft open. No token or Authorization value appears in their output or errors.
  `pr-review` on the GitHub adapter reports "unsupported".
- `obtain-review`'s round-brief `##` sections appear in the tool's template as an ordered prefix. The tool's extra
  sections follow them, and a pinned list names those extras: today, the in-app evidence and context-references
  sections.
- A scan of every `run-delivery` provenance output and every captured command output finds no host term outside marked
  examples. The host terms are a pinned list in the test. It holds at least the session, messaging and publishing
  tool names the old skill text used, `mcp__`, `.claude/` and `~/.claude`. Host projection paths are allowed only
  inside marked examples.

### U3. The setup check

**Goal:** `doctor` per C7.

**Posture:** `test-first`.

**Ownership:**
- the `doctor` command;
- its tests;
- the `doctor` section of `SKILL.md`;
- the troubleshooting entries for its messages.

**Required scenarios:**
- Each row has two cases:
  - an `ok` case;
  - a failing case that asserts the fix line.
- Removing each prerequisite in turn from a clean fixture yields exactly one line that is neither `ok` nor `skipped`.
- With a lane holding the lease, `--run-adapters` reports `skipped (lease held by …)` at once.
- Without `--run-adapters` and `--run-gates`, `doctor` leaves no new process, lock or tracked change behind.

### U4. The adoption guide and skill text

**Goal:** The guide per C8, and `SKILL.md` as the command reference that points to it.

**Posture:** `sensor-only`, with the sensors below.

**Dependencies:** U3, whose messages the troubleshooting section is keyed by.

**Ownership:** `references/adopting.md`, `SKILL.md` apart from U3's section, and their pins.

**Substitutions.** The guide's blocks target a real adopter, and the suite reaches no forge or application. The
fixture-adopter test may change only these, and only through documented configuration overrides or named
placeholders:
- `forge.argv` → the fake forge adapter;
- the in-app `build`, `serve` and `check` argv → the fixture fakes;
- the remote → a local bare repository;
- the issue URL host;
- `<story-key>` placeholders → fixture keys;
- `doctor` → `doctor --offline`.

The list is pinned in the test. Everything else in the blocks is used verbatim, and the test fails if a block carries
an adopter-specific value outside a named placeholder.

**Required scenarios:**
- The fixture-adopter test extracts the guide's marked configuration blocks into an empty repository, applying only
  the substitutions above. `doctor` is then clean.
- The test runs the guide's marked command blocks through close-out, under the same substitutions.
- Every command the guide names exists in the tool's parser. A renamed command fails the suite.
- The operating rules in section 8 are pinned.
- Registry validation passes with the guide's path conventions.

### U5. Qualification, runtime and release

**Goal:** The new profile ships, with fresh qualification for every profile.

**Dependencies:** U1–U4 and P1–P5.

**Ownership:**
- the marker's deletion;
- fresh `run-delivery-v1` records for core, bootstrap, provider and linear;
- the current-set pointers in the qualification tests;
- the runtime rebound to the `run-delivery` projection;
- the product zip and release files under `qualifications/product/`;
- `docs/releases/run-delivery-v1.md` and the README's profile and install text;
- the linear live host attestation;
- the release cut;
- re-earning the sets with the plan chapter's `delivery-views` and `run-delivery` changes in the same pass, and the new
  schema output in the `delivery-views`, `linear` and `run-delivery` profile archives, and in the `linear` and
  `run-delivery` products;
- the release notes describing the plan chapter and the `delivery-plan/1` input.

**Required scenarios:**
- With the marker deleted, registry validation and the full suite pass, with nothing skipped except the named tool
  skips.
- Every profile's qualification records its scenarios.
- Release build and verify pass for all five profiles.
- The plan chapter renders from the installed generation for the fixture delivery the guide drives, with a
  `plan.json` that passes `check-plan`.
- From a writable installed `run-delivery` generation, the fixture adopter runs the guide's blocks through close-out.
  The lifecycle `status` then reports no divergence, and no process the run started is left.

### P1. The input contract and validator

Within P1–P5, C*n* means `2026-10-30-plan-chapter.md`'s C*n*, not this plan's.


**Ownership:**
- `skills/delivery-views/references/delivery-plan.schema.json`;
- the validator and `check-plan` in `views_common.py`;
- `tests/test_delivery_views_plan.py`;
- the new output in the `delivery-views`, `linear` and `run-delivery` profiles' `expectedFiles`, and the catalog entry;
- the hand-added `provenance.lock.json` entry, then a run of the provenance writer;
- the release-integration pin on delivery-views files.

**Required scenarios:**
- A valid fixture is accepted.
- Each of these is refused with its key path named and no values echoed:
  - an unknown key;
  - an overlong text;
  - an over-count list;
  - a file over 1 MiB;
  - a duplicate criterion id;
  - an unknown record kind;
  - a `covers` naming an unknown criterion;
  - a `done` entry without `head`;
  - a `waived` entry without `reason`.
- `check-plan` passes on a valid file, and fails with the same messages on an invalid one.

### P2. The renderer

**Ownership:** `render.py`'s line-map path and its tests.

**Required scenarios:**
- With no `plan.json`, the C3 no-change guarantee holds against every existing golden.
- With an invalid `plan.json`, the page renders as if it were absent, plus the one-line chapter.
- An oversized plan stays within its share, clipped per C10, and says so.

### P3. The page

**Ownership:**
- `line-map.js` and `line-map.css`;
- the page tests;
- `golden.test.mjs`'s case list;
- `support.mjs`, whose `renderPage` places a `plan.json`;
- the rail-label pin in `line-map.test.mjs`;
- two synthetic fixture deliveries under `tests/fixtures/delivery-views/line-map/`.

**Approach:** characterize first.

**Required scenarios.** These are page tests with a fixed time zone, a fixed clock and empty storage.
- **Default content:** per C8, with nothing else unfolded.
- **Flags:** each of C7's eight, plus "nothing needs you", including one finish line covering no criteria and one
  covering `"all"`.
- **C4 shapes:**
  - a re-hand-off after a merge;
  - a stopped candidate;
  - a pending acceptance;
  - a batch landing;
  - `merged_unverified`;
  - a run-backed unit;
  - a native-review unit;
  - an approval at a stale head, using a journal-only fixture;
  - a synced candidate whose base moved, which keeps its review and producer proofs `done`;
  - a native-review unit with captured evidence, synced onto a moved base and then merged, which stays proven;
  - a merged unit, which is proven;
  - a unit with no recorded review;
  - a one-lens round where two lenses are required.
- **Stale producer evidence** after a re-hand-off.
- **Live updates:**
  - a pushed merge redraws the chapter;
  - a pushed "Plan updated:" entry naming a new `updated` shows the reload notice;
  - a page rendered after that entry shows none.
- **The switch:** as approved and as amended, including a removed unit.
- **Folds:** each fold, remembered without storage.
- **The stop-to-unit link,** for open and proven units.
- **The phone layout,** and both themes.
- **Escaping:** a subject, criterion and `why` containing `</script>`, `"` and an `<img onerror>`, and a `javascript:`
  ref.
- **Real record shapes.** The record documents for the synced and merged shapes come from `delivery_state.py show`, run
  on a record whose runner sync produced a new `syncedHead`. They are not written by hand.
- **Goldens** for both fixtures, one with three record columns only. The first-adopter term scan extends to these
  fixtures.

### P4. The producer

**Ownership:**
- `run_delivery.py`:
  - the optional delivery-file keys;
  - `lead-plan change` and `lead-plan why`;
  - change recording on registration;
  - drift detection;
  - the `lead-init` snapshot and record-finish stripping;
  - the plan build under one lock;
  - the "Plan updated:" journal entry;
- its tests;
- the two marked blocks in `skills/run-delivery/references/adopting.md`;
- the `doctor` row;
- the U3 and U4 pins it moves: the doctor's row count and one-line-per-prerequisite tests, the guide's command list,
  and the fixture adopter's blocks.

**Dependencies:** P1 and U4 merged.

**Required scenarios:**
- `lead-init` succeeds with `covers` and `label` present, and the record holds only `{id, text, met}`.
- `lead-plan change` records `before` and `after`, edits and commits the delivery file, prints the push command, and
  refuses on a dirty or diverged checkout.
- `--kind finish` adds the line to the record.
- Two sweeps with no change write nothing: no `plan.json`, no journal entry and no changes-log entry.
- A delivery initialized before criteria existed gets a snapshot marked "taken after the delivery started".
- A registered mid-delivery story records an `added` change, and `lead-plan why` completes it.
- A hand edit to a criterion appears as `unrecorded`.
- A lane's `app-check` or `app-video` changes the plan only through the next lead-side rebuild.
- Two concurrent rebuilds don't lose an update.
- Every written `plan.json` passes P1's validator.
- P4 adds two marked blocks to the guide: a delivery-file block declaring `criteria`, a finish line with `covers` and
  `outcome`, and a `lead-plan change` command block. The fixture adopter's block list and its substitution pin follow
  them. After those blocks run, the fixture
  delivery has a `plan.json` that passes `check-plan`.
- A delivery without criteria writes no plan, and `doctor` shows the `skipped` row.

### P5. Skill text and registry descriptions

**Ownership:**
- `delivery-views/SKILL.md`: the input, the chapter, and C4, C6, C7 and C9;
- `run-delivery`'s guide: writing criteria and recording changes;
- the description sentences in the three profiles and the catalog.

- **The guide's prose** around P4's marked blocks.

**Required scenarios:** pins on C7's rules and C2's field list.

### U6. Sync with the default branch, and re-earn

**Goal:** The integration branch builds on the default branch's latest release, with core, provider and bootstrap
re-earned and the Linear collector input re-frozen for the archives that ship.

**Dependencies:** U5, and the default branch's head when U6 starts.

**Ownership:** U5's, plus the default branch's changes to `skills/orchestrate-work/**` that the merge brings in.

**Required scenarios:**
- The default branch is merged in; its last release's records become history and `run-delivery-v1` stays current.
- Core, provider and bootstrap replay exactly against the rebuilt archives.
- The Linear collector binding names the rebuilt core and linear archives, with fresh opaque references.
- Registry validation, provenance and the full suite pass with the marker naming only `linear`.
- The release notes' collection steps name the re-frozen binding.

## Dependency Graph

U1 → U2 → U3 → U4 → U5, with the plan chapter's units joining before U5:

- P1 → P2 → P3, and P1 → P4 once U4 has merged. P5 follows P3 and P4. U5 follows P5. U6 follows U5 (Amendment 2).
- P3 and P4 touch disjoint files and may run in parallel lanes.

- **U1–U4 are sequential:** U4 depends on U3's messages.
- **P1 and U4 both edit `provenance.lock.json`.** P1 rebases onto U4's merge before its final provenance run.
- **One integration branch.** Registry validation refuses the qualification-pending marker on any change that
  targets the default branch. So U1–U5 land on one integration branch, and it merges once, after U5 deletes the
  marker, as in the precedent.
- **Shared in every unit:**
  - `provenance.lock.json`: after each change to a skill file, run the provenance writer; on a conflict, take either
    side and run the writer again (AGENTS.md);
  - the serial test module;
  - the worktree-private `.venv`.

## Representative End-to-End Scenarios

1. **Fresh adopter.** An empty fixture repository with a release installed runs `doctor` and gets one `missing` line
   per required file, each with its fix. After the guide's configuration blocks are written in, `doctor` is clean.
2. **Whole delivery from an installed generation.** The fixture runs the guide's command blocks: setup through
   close-out, including a reconcile sweep, the merge queue and the renderer. Afterwards:
   - nothing is left running;
   - the generation shows no divergence.
3. **First adopter's existing setup.** A repository still on the old names keeps working, with each one reported by
   `doctor`:
   - `.agents/flow.json`;
   - a `delivery-flow-delivery/1` delivery file;
   - a baseline stamped `.flow-build.json`;
   - `DELIVERY_FLOW_LOCAL`.
4. **Missing tool.** With ffmpeg absent and a story marked for video, `doctor` fails that one row with an install hint.
   `app-video` refuses with the same message.
5. **Forge access.**
   - Bitbucket token unset: the forge row fails and names the variable.
   - `--offline`: the forge row is skipped.
   - Token set: `find` succeeds and write access reads "not verified".
6. **Busy lease.** While a lane holds the in-app lease, `doctor --run-adapters` reports the holder without waiting.

## Repository Sensors

- `python3 scripts/bootstrap-test-environment.py`
- `.venv/bin/python scripts/validate-registry.py`
- `.venv/bin/python tests/test_registry.py`
- `.venv/bin/python scripts/run-tests.py`: `tests/test_run_delivery.py` runs serially, and the marker skips apply until
  U5.
- `.venv/bin/python scripts/update-provenance.py --check`
- `scripts/check-core-leakage.py`, for core. `run-delivery` itself is covered by its own leakage scan in
  `tests/test_run_delivery.py`. That scan uses two lists:
  - the corpus leakage patterns, minus the provider boundary;
  - a pinned list of first-adopter terms.
- The four qualification scripts, and release build and verify, at U5.
- For the plan chapter:
  - the page-test lane (jsdom, Node 20 or newer);
  - `views_common.py check-plan` on the fixtures.
- CI on ubuntu, macos and windows. The module skips on windows.

## Risks and Mitigations

- **Behaviour drifts while moving.** U1 makes no behaviour change the self-test can see, and proves it before any
  rename.
- **The suite slows or flakes.** The ported cases run as subprocesses, serially, and stop every process they start on
  timeout.
- **A release-qualification gap ships.** The marker blocks a release and blocks the default branch until U5 re-earns
  every set.
- **Linear's attestation needs a live host.** The lead collects it. Without it the release cannot be cut, and that
  stays a visible blocker.
- **Host or first-adopter terms leak in.** The U2 host scan and the U1 leakage scan cover every shipped output and
  command output.
- **`doctor` reports clean while a delivery would fail.** The row list is derived from C6 and the configuration the
  commands use. Forge write access is reported as unverified, never as `ok`. U4's fixture adopter runs a whole delivery
  after a clean `doctor`.
- **Secrets reach records or published pages.** C10.
- **A writing forge client in the corpus.** Draft-only, behind the authority grant, with no token in output, tested in
  U2.

## Approval Handoffs

1. This plan's approval, after the lenses align. That approval includes C5's writing forge adapters.
2. The owner's approval to contribute the tool's code, recorded in provenance (given 2026-10-29).
3. The linear live host attestation, collected by the lead in U5.
4. Cutting the release.
5. Pushing the branch and opening the change request, which are the operator's call.

## Observable Finish Line

1. **A release.** A `run-delivery` release, with fresh qualification for core, bootstrap, provider and linear. From a
   writable installed generation, its tool runs a whole delivery and leaves no divergence and no process behind.
2. **The suite.** `tests/test_run_delivery.py` passes in the full suite on POSIX, with only named tool skips. It
   covers:
   - the 17 characterized cases;
   - U2's behaviour tests;
   - U3's rows;
   - the guide-driven fixture adopter.
3. **The guide.** An empty fixture repository reaches a clean `doctor` and a closed delivery by running only the
   guide's marked blocks.
4. **No leakage.** The leakage scan and the host scan find no first-adopter term, and no host tool name outside marked
   examples, in any `run-delivery` output.
5. **The plan chapter.** Finish lines 1–4 of `2026-10-30-plan-chapter.md` hold, and the release carries it.
