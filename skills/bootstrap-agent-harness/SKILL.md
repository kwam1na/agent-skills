---
name: bootstrap-agent-harness
description: Use when creating a new repository, app, service, or monorepo that should be agent-ready from the start with orientation docs, machine-readable validation coverage, runtime scenarios, drift detection, and CI-backed harness maintenance.
---

# Bootstrap Agent Harness

## Overview

Create the harness before feature work. The harness is required infrastructure: it gives future agents a place to start, tells them what validations apply to changed files, and detects when docs or coverage drift away from the code.

A repo is not "bootstrapped" just because it has docs and command names. If the harness advertises `harness:*` commands, those commands must execute real logic or be clearly omitted from the supported flow.

The output is not just scaffolding. The output is a repository with:

- a navigation layer
- local operating manuals for each app or service
- machine-readable validation coverage
- runtime behavior scenarios
- drift sensors
- CI and artifacts for harness health

## When to Use

Use this skill when:

- bootstrapping a brand-new repo
- instantiating a new app or service inside a larger repo
- upgrading an existing repo so agents can work in it reliably
- creating a reusable project template for future agent-built systems

Do not use this skill for:

- one-off code changes inside an already-harnessed repo
- pure library packages with no need for runtime checks beyond ordinary tests, unless you are adding a full harness layer

## Bootstrap Modes

Default to `complete bootstrap`.

- `complete bootstrap`
  The repo may be treated as harness-ready. Published commands, docs, validation maps, CI, and generated artifacts must all be truthful and runnable.
- `scaffold-only bootstrap`
  Use only when the user explicitly asks for partial setup or time-boxed scaffolding. In this mode, docs must clearly mark incomplete areas as planned, and unsupported commands must not be presented as part of the supported operator flow.

Never silently blur these modes. Most future drift starts when scaffolded placeholders are allowed to masquerade as a complete harness.

## Required Outcomes

Before you consider the repo bootstrapped, it must have:

- a repo-level `AGENTS.md`
- a generated repo navigation layer, ideally a graph/wiki
- one local `AGENTS.md` per harnessed app or service
- a `docs/agent/` folder per harnessed app or service
- generated discovery indexes
- a machine-readable validation map per harnessed app or service
- repo-level harness commands for generation, review, audit, behavior, and repair
- CI wiring that runs the harness and publishes artifacts
- a configured remote with the intended default branch established early enough that later PRs and worktrees do not stack on the wrong base
- at least one fresh-checkout or fresh-worktree verification run proving the documented bootstrap flow works outside the author's dirty tree

## Recommended Layout

```text
repo/
├── AGENTS.md
├── README.md
├── docs/
├── graphify-out/ or other generated graph/wiki output
├── scripts/
│   ├── harness-app-registry.*
│   ├── harness-generate.*
│   ├── harness-check.*
│   ├── harness-review.*
│   ├── harness-audit.*
│   ├── harness-behavior.*
│   ├── harness-behavior-scenarios.*
│   ├── harness-inferential-review.*
│   ├── harness-runtime-trends.*
│   ├── harness-scorecard.*
│   ├── harness-janitor.*
│   ├── harness-test.*
│   ├── graphify-check.*
│   └── graphify-rebuild.*
├── artifacts/
└── apps-or-packages/
    └── <app-or-service>/
        ├── AGENTS.md
        └── docs/
            └── agent/
                ├── index.md
                ├── architecture.md
                ├── testing.md
                ├── code-map.md
                ├── route-index.md or entry-index.md
                ├── test-index.md
                ├── key-folder-index.md
                ├── validation-guide.md
                └── validation-map.json
```

Adapt the exact directory names to the repo, but preserve the structure: repo-level routing, app-level operating docs, generated indexes, and machine-readable validation coverage.

## Workflow

### 0. Lock Toolchain And Repo Defaults Early

Before writing detailed harness docs or generated artifacts, resolve:

- the primary language and package/tooling stack
- the intended repo archetype
- the remote hosting target when one exists
- the default branch, usually `main`

Do this early enough that you do not have to rewrite the bootstrap around a later stack pivot or branch rename.

If the repo will be pushed during bootstrap:

- create or connect the remote before long-lived ticket branches stack on top of the bootstrap branch
- set the default branch before opening PRs that future agents may branch from
- verify local `main` and `origin/main` are aligned before claiming the repo is in a stable bootstrap state

### 1. Build the Navigation Layer First

Create repo-level `AGENTS.md` that tells future agents:

- where to start
- whether a generated graph/wiki exists
- which app or service-level `AGENTS.md` to open next
- when graph/wiki artifacts must be regenerated

If the repo supports it, create a generated graph/wiki layer with:

- a graph report
- a wiki index
- per-app or per-package landing pages

Agents should navigate generated orientation docs before scanning raw files.

### 2. Register Harnessed Apps and Services

Create one central harness registry such as `scripts/harness-app-registry.*`.

For each harnessed target, define:

- label
- repo path
- archetype: `webapp`, `service`, `worker`, or `library`
- onboarding status
- audited roots
- required docs
- key folder groups
- validation surfaces

This registry is the source of truth for generation, audit, review selection, scorecards, and documentation expectations.

### 3. Create Local Operating Manuals

For each harnessed app or service, create:

- `AGENTS.md`
- `docs/agent/index.md`
- `docs/agent/architecture.md`
- `docs/agent/testing.md`
- `docs/agent/code-map.md`

Each file should have a narrow job:

- `AGENTS.md`: entrypoint and local rules
- `index.md`: scope, boundaries, and common validations
- `architecture.md`: main seams, entrypoints, and “edit here, not there” guidance
- `testing.md`: validation ladder and sensor failure guidance
- `code-map.md`: ownership map for important runtime and test surfaces

### 4. Generate Discovery Docs

Create a generator such as `harness:generate` that writes the derived docs.

Do not generate a large discovery layer ahead of the executable kernel. The minimum honest sequence is:

1. real registry
2. real `harness:generate`
3. real `harness:check`
4. then generated docs and coverage artifacts

Required generated docs:

- `route-index.md` for route-driven apps
- `entry-index.md` for services, workers, or non-route packages
- `test-index.md`
- `key-folder-index.md`
- `validation-guide.md`
- `validation-map.json`

Every generated doc must clearly say it is generated and should be regenerated rather than edited by hand.

Generation should fail if the harness references commands, paths, or package metadata that do not exist.

If the repo is only scaffolded, generated docs must not imply that placeholder commands are already trustworthy.

### 5. Define Validation Coverage as Data

For each harnessed app or service, create `docs/agent/validation-map.json`.

Each validation surface should declare:

- `name`
- `pathPrefixes`
- `commands`
- optional `behaviorScenarios`
- optional rationale or note

Rules:

- every live surface under the audited roots must be covered
- changed files without coverage are a harness bug
- live files without coverage are a harness bug
- the testing guide must explain how to repair both cases

Example:

```json
{
  "workspace": "@example/webapp",
  "packageDir": "packages/example-webapp",
  "surfaces": [
    {
      "name": "Route and component edits",
      "pathPrefixes": [
        "packages/example-webapp/src/routes",
        "packages/example-webapp/src/components"
      ],
      "commands": [
        { "kind": "script", "script": "test" },
        { "kind": "script", "script": "lint:architecture" }
      ],
      "behaviorScenarios": ["webapp-shell-boot"]
    }
  ]
}
```

### 6. Implement Drift Sensors

Add repo-level commands equivalent to:

- `harness:generate`
- `harness:check`
- `harness:review`
- `harness:audit`
- `harness:janitor`

Required behavior:

- `harness:check`
  validates harness docs, required links, documented scenarios, path references, and referenced commands
- `harness:review`
  reads changed files and runs the smallest honest validation set from `validation-map.json`
- `harness:audit`
  scans live surfaces and fails on stale docs, stale map references, or uncovered code
- `harness:generate`
  regenerates derived docs and coverage artifacts
- `harness:janitor`
  runs drift sensors in report mode and optionally performs safe repair steps such as regeneration or graph rebuild

Rules:

- once a command is documented as supported in `README.md`, `AGENTS.md`, or `docs/agent/*`, it must stop returning placeholder success output such as `stub:*`
- if a command is not implemented yet, omit it from the supported bootstrap flow rather than teaching future agents to trust a fake green path
- review ordering must honor prerequisites; for example, regeneration must happen before freshness checks when generated artifacts are in scope

### 7. Add Runtime Sensors

Create a scenario inventory such as `scripts/harness-behavior-scenarios.*`.

Each scenario should define:

- name
- description
- processes to boot
- readiness checks
- browser, HTTP, CLI, or queue flow steps
- runtime signal expectations
- assertions
- cleanup
- latency thresholds

Name scenarios after user-observable behavior, not internal implementation details.

Good names:

- `webapp-shell-boot`
- `checkout-validation-blocker`
- `service-local-request-response`
- `worker-job-completes`

Runtime sensors must be able to:

- boot required processes
- wait for readiness through HTTP, log, or custom checks
- drive a realistic interaction
- inspect runtime signals
- emit a machine-readable report

Persist reports under `artifacts/`. Support optional video or trace capture for browser scenarios.

If a runtime scenario is named in the supported bootstrap flow, README, or testing guide, it must either:

- run in CI or the local PR-equivalent validation path, or
- be explicitly marked `manual-only` in both docs and tests

### 8. Add Higher-Level Sensors

Add commands equivalent to:

- `harness:inferential-review`
- `harness:runtime-trends`
- `harness:scorecard`

Responsibilities:

- inferential review produces blocking findings and machine-readable artifacts
- runtime trends aggregate behavior-report history
- scorecard summarizes documentation health, inferential status, runtime trends, and graph freshness

If inferential review has both deterministic and semantic lanes, keep the deterministic lane authoritative and use the semantic lane as shadow telemetry unless there is a clear reason to make it blocking.

### 9. Wire CI and Local PR Validation

CI should:

- run harness implementation tests
- run harness checks
- run touched-file review against the PR base
- run full-surface audit
- run inferential review
- generate scorecards
- check graph freshness
- upload harness artifacts

Expose one umbrella command for PR-equivalent local validation.

Additional requirements:

- CI install strategy must match lockfile reality. If the repo has no lockfile yet, do not wire `npm ci` or lockfile-coupled cache assumptions as if the repo were already lockfile-stable.
- `validate:pr` should mirror the real PR gate closely enough that a local green run is meaningful.
- any behavior scenario described as part of the supported bootstrap flow must be included in CI or `validate:pr`, unless it is explicitly documented as manual-only.
- artifact upload paths must include the generated docs, graph output when applicable, and runtime behavior artifacts.

### 10. Test the Harness Itself

Create tests that verify:

- registry entries resolve to real files
- generated docs contain required sections and links
- validation maps reference real paths and commands
- review selection logic covers changed files correctly
- audit logic detects missing coverage
- scenario inventories are documented in repo and local testing docs
- CI and pre-push wiring include required harness steps
- documented bootstrap commands are actually executed by the validation ladder, not merely mentioned in prose
- a fresh checkout or temp-workspace bootstrap flow succeeds without relying on author-local state

The harness must be self-policing. If it cannot detect its own drift, it will rot.

## Archetype Rules

### Webapp

- generate `route-index.md`
- include browser-flow runtime scenarios
- document entrypoints, route roots, layout boundaries, and API boundaries

### Service

- generate `entry-index.md`
- include request/response and health-check scenarios
- document bootstrap, server entrypoint, and operator probes

### Worker

- generate `entry-index.md`
- include queue or job-completion scenarios
- document scheduler, retry, and idempotency boundaries

### Library

- focus on public API surfaces and package tests
- skip runtime scenarios unless the library has a runnable fixture harness

### CLI Or Tooling Repo

- generate `entry-index.md`
- treat command entrypoints, registry modules, scenario inventories, and generated docs as first-class validation surfaces
- include at least one CLI or operator-visible smoke scenario
- document which commands are part of the supported bootstrap path versus broader maintenance tooling

## Guardrails

- Do not treat uncovered changed files as permission to skip validation.
- Do not hand-maintain indexes that can be generated.
- Do not document runtime scenarios in prose only; represent them in code or structured config.
- Do not let local docs and machine-readable coverage drift independently.
- Do not rely on a single repo-level README as the only agent onboarding surface.
- Do not merge a repo as "bootstrapped" while supported harness commands still return placeholder success output.
- Do not let README or onboarding docs promise a bootstrap flow that CI and `validate:pr` do not actually exercise, unless the manual-only status is explicit.
- Do not leave local `main` behind `origin/main` after bootstrap merges if later ticket work will branch from it.

## Completion Checklist

Bootstrap is complete only when:

- an agent can start at repo-level docs and reach the correct local harness without guesswork
- every harnessed app or service has the required docs
- every audited live surface is covered by a validation map
- changed files can trigger the right validations automatically
- uncovered code is detected by audit
- each executable app or service has at least one runtime scenario
- inferential and scorecard artifacts can be produced
- CI publishes harness artifacts
- graph/wiki outputs are generated and documented when the repo supports them
- documented harness commands execute real logic rather than stubs
- the supported bootstrap flow in `README.md` matches the real CI or `validate:pr` gate, except where manual-only steps are explicitly labeled
- a fresh checkout or fresh worktree run proves the bootstrap path and PR-equivalent validation path work without author-local leftovers

## Deliverables

When applying this skill, produce:

- the repo-level and local `AGENTS.md` files
- all `docs/agent/*` files
- the harness registry
- generation, review, audit, behavior, scorecard, and repair commands
- runtime scenario inventory
- CI wiring
- harness tests
- a short README section explaining how to run the harness
