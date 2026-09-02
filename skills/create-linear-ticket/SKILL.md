---
name: create-linear-ticket
description: Use when approved work needs to be split into atomic tracked work items through the optional tracker capability.
---

# Decomposing Work Into Tracked Items

Turn approved work into tracked items small enough to ship independently and
clear enough to hand straight to execution. Decomposition sits between planning
and implementation: use `$plan-work` when the plan itself is missing, and
`$execute-linear-ticket` when the items already exist and the caller wants
implementation.

## Delivery Posture

Apply `$compound-delivery-kernel` when shaping items. The tracker owns the work
record, the repository owns sensors, and the skills system owns the delivery
workflow. Do not encode the agent workflow into repository instructions unless
the repository is missing a sensor that should become a follow-up.

## When to Use

- The caller asks to create tracked items from approved work or a validated plan.
- The caller wants a plan turned into parallelizable work items.
- The caller wants broad work decomposed into atomic implementation items.

Do not use this skill when:
- the relevant items already exist
- the caller wants implementation rather than decomposition
- the main problem is execution hygiene rather than item shape

## Tracker Boundary

Reach the tracker only through the neutral operations that
`$linear-tracker-adapter` maps. That adapter owns properties resolution,
outcome normalization, and mutation safety; this skill supplies no
provider-specific resolution of its own.

- `resolve-context` establishes the target context before any mutation. Treat
  the returned references as required context and report them in the handoff.
- `create-work` records one decomposed item.
- `link-dependencies` records true blockers between created items.

Carry a stable idempotency key per mutation. When an outcome is not confirmed,
stop and report it; never repeat the mutation. Consume only the normalized
outcome, never a raw host payload.

This skill has no operation for rewriting an existing item's body or labels.
When an existing item is wrong, hand that off rather than editing it here.

## Workflow

1. Confirm intent.
- If the items already exist, stop and use `$execute-linear-ticket`.
- If planning, decomposition, and implementation are mixed, finish decomposition
  first and then hand off to `$execute-linear-ticket`.

2. Produce a concrete plan.
- Prefer `$plan-work`.
- If a bounded plan is already approved, use `references/atomic-plan-template.md`
  to render it as a checklist.
- The output should be a checklist of concrete implementation outcomes.
- Include the execution posture for each behavior-bearing task: `test-first`,
  `characterization-first`, or `sensor-only`.
- Include the repository sensors expected to prove the task: targeted tests,
  broader suites, typecheck, build, lint, review commands, runtime scenarios, or
  other project-specific sensors.

3. Convert the plan into item candidates.
- Default to one actionable checklist item per tracked item.
- Merge only when implementation and validation are inseparable.
- Split whenever outcomes can be shipped and tested independently.
- Preserve execution posture, test scenarios, expected sensors, and compounding
  opportunities from the plan in each candidate.

4. Enforce atomicity.
- Each item should have one shippable outcome.
- Each item should be independently mergeable and testable.
- Record dependencies only for true blockers.
- Do not force frontend or backend splits unless the work naturally separates.
- If a feature materially changes what the repository does, create an item to
  refresh the repository and agent documentation so they capture its new
  standing.
- Atomic items do not require one change set per item. If several items will all
  touch the same generated or derived artifacts, keep them separate in the
  tracker but mark them as a coordinated batch that can land through one
  integration change set.

5. Detect generated-artifact batches.
- Look for shared outputs that are cheap to regenerate but expensive to merge
  repeatedly: codegen, graph artifacts, indexes, snapshots, lockfiles, or other
  derived repository state.
- If multiple items would all churn those surfaces, prefer parallel branches or
  worktrees per item followed by one integration branch that regenerates the
  shared artifacts once.
- Say so explicitly in the item bodies or the handoff so execution does not
  default back to one change set per item.

6. Build deterministic item bodies.
- Use `references/atomic-ticket-template.md`.
- Every item should include `Scope`, `Acceptance Criteria`, `Test Scenarios`,
  `Execution Posture`, `Expected Sensors`, and `Compounding Opportunity`.
- Add security, authorization, or idempotency scenarios only when the work
  actually touches those areas.
- `Execution Posture` defaults to `test-first` for new behavior and bug fixes,
  `characterization-first` for unclear legacy behavior, and `sensor-only` only
  for pure documentation, generated artifacts, configuration, or mechanical
  changes with no behavior.
- `Expected Sensors` should name the project checks the executor should run;
  keep them project-specific when known, and do not invent repository commands
  that have not been discovered.
- `Compounding Opportunity` should name likely reusable learnings, missing
  sensors, or skill updates; write `None expected` when there is none.

7. Record the items.
- Establish context with `resolve-context` first.
- Record each candidate with `create-work`, then record true blockers with
  `link-dependencies`.
- Use `resolve-context` to check for a near-duplicate active item before
  recording a new one.
- If the tracker capability is absent, unconfigured, or blocked, report the
  normalized outcome, perform no mutation, and hand off the undecomposed
  candidates so the caller can record them.

8. Return the execution handoff.
- Include resolved context, recorded items, dependency map, and assumptions.
- If the items form a coordinated generated-artifact batch, say so directly and
  recommend a single integration change set after parallel execution.
- If implementation is next, say `Use $execute-linear-ticket for implementation.`

## Output

- `Resolved Context`: the references returned by `resolve-context`
- `Plan Source`: `Planning workflow` or `Approved plan`
- `Recorded Items`: title and returned reference per item
- `Execution Plan`: `Can Start Now` and `Blocked`
- `Integration Strategy`: `One change set per item` or `Single integration change
  set after parallel execution`
- `Delivery Posture`: execution posture and expected sensors by item
- `Compound Notes`: likely learning, skill, or follow-up sensor opportunities
- `Assumptions`: scope splits, property choices, or context clarifications
- `Omitted Tracking`: any mutation that was not performed, and why

## Guardrails

- Optimize for minimum dependency chains and maximum parallel execution.
- Do not decompose vague scope; if the work is not concrete enough to become a
  checklist, plan first.
- Do not create umbrella items when the plan yields separable outcomes.
- Avoid implementation detail that does not help define the item.
- Treat repository and agent documentation refresh as required follow-up work
  when a feature changes capabilities, workflows, architecture, or other durable
  behavior.
- Prefer a single integration change set when separate ones would mostly fight
  over regenerated artifacts rather than represent meaningful review boundaries.
- Do not make tracked items into implementation scripts. Capture outcomes,
  tests, sensors, posture, and boundaries; let execution choose the path.
- Do not let an item omit tests for behavior-bearing work unless the posture
  explains why characterization or sensor-only validation is more appropriate.
- Decomposition is done when the tracker is up to date and the next execution
  order is obvious.
