---
name: create-linear-ticket
description: Use when approved work needs to be split into atomic, parallelizable Linear tickets.
---

# Creating Linear Tickets

Turn approved work into Linear issues that are small enough to ship independently and clear enough to hand straight to execution. If the tickets already exist and the user wants implementation, stop and use `$execute-linear-ticket`.

## Delivery Posture

Apply `$compound-delivery-kernel` when shaping tickets. Linear tickets should describe the work to ship, while the skills system owns the delivery workflow. Repos provide sensors such as tests, validation maps, harness review commands, runtime checks, docs drift checks, and CI; do not encode the agent workflow into repo instructions unless the repo is missing a sensor that should become a follow-up.

## When to Use

- The user asks to create tickets from approved work or a validated plan.
- The user wants a plan turned into parallelizable Linear issues.
- The user wants broad work decomposed into atomic implementation tickets.

Do not use this skill when:
- the relevant tickets already exist
- the user wants implementation rather than ticket creation
- the main problem is execution hygiene rather than ticket shape

## Shared Context

Apply `references/linear-project-resolution.md` before any Linear mutation. Treat the resolved team/project as required context and report it in the handoff.

Use this resolution order before asking the user for context:
- If a Linear issue ID is present, call `get_issue` first and derive the team/project from the issue.
- If no issue id is present, look for explicit project/team names and validate them with `get_team` and `get_project`.
- If neither source resolves context, inspect the current working directory the skill was invoked from.
- Build candidate project names from the current directory basename, the git repo root basename when it differs, and obvious workspace metadata such as `package.json` names.
- If exactly one confident project match exists, use it automatically and report that it was cwd-derived.
- If the issue exists but has no project, stop and ask instead of silently falling back.
- If the named or derived project does not exist in Linear, stop and surface the mismatch clearly.

## Workflow

1. Confirm intent.
- If the issues already exist, stop and use `$execute-linear-ticket`.
- If the user is mixing planning, ticket creation, and implementation, finish ticket creation first and then hand off to `$execute-linear-ticket`.

2. Produce a concrete plan.
- Prefer `superpowers:writing-plans`.
- If it is unavailable, use `references/atomic-plan-template.md`.
- The output should be a checklist of concrete implementation outcomes.
- Include the execution posture for each behavior-bearing task: `test-first`, `characterization-first`, or `sensor-only`.
- Include the repo sensors expected to prove the task: targeted tests, broader suites, typecheck/build/lint, harness or review commands, runtime scenarios, CI-equivalent checks, or other project-specific sensors.

3. Convert the plan into ticket candidates.
- Default to one actionable checklist item per ticket.
- Merge only when implementation and validation are inseparable.
- Split whenever outcomes can be shipped and tested independently.
- Preserve execution posture, test scenarios, expected sensors, and compounding opportunities from the plan in each ticket candidate.

4. Enforce atomicity.
- Each ticket should have one shippable outcome.
- Each ticket should be independently mergeable and testable.
- Record dependencies only for true blockers.
- Do not force frontend/backend splits unless the work naturally separates that way.
- If a feature materially changes what the repo does, create a ticket to refresh the repo docs and agent docs so they capture the repo's new standing after the feature lands.
- Atomic tickets do not require one PR per ticket. If several tickets will all touch the same generated or derived artifacts, keep the tickets separate in Linear but mark them as a coordinated execution batch that can land through one integration PR.

5. Detect generated-artifact batches.
- Look for shared outputs that are cheap to regenerate but expensive to merge repeatedly: codegen, graph artifacts, harness docs, indexes, snapshots, lockfiles, or other derived repo state.
- If multiple tickets would all churn those surfaces, prefer parallel implementation branches or worktrees per ticket followed by one integration branch that regenerates the shared artifacts once.
- Call this out explicitly in the ticket bodies or creation handoff so execution does not default back to one PR per ticket.

6. Build deterministic ticket bodies.
- Use `references/atomic-ticket-template.md`.
- Every ticket should include `Scope`, `Acceptance Criteria`, `Test Scenarios`, `Execution Posture`, `Expected Sensors`, and `Compounding Opportunity`.
- Add security, authorization, or idempotency scenarios only when the work actually touches those areas.
- `Execution Posture` must default to `test-first` for new behavior and bug fixes, `characterization-first` for unclear legacy behavior, and `sensor-only` only for pure docs, generated artifacts, configuration, or mechanical changes with no behavior.
- `Expected Sensors` should name the project checks the executor should run; keep them project-specific when known, but do not invent repo commands that have not been discovered.
- `Compounding Opportunity` should name likely reusable learnings, missing sensors, or skill updates; write `None expected` when there is no meaningful opportunity.

7. Create or update in Linear.
- Verify access to the resolved context first.
- Prefer direct Linear MCP operations.
- If mutation fails in-session, use the fallback in `references/linear-execution-notes.md`.
- Check for near-duplicate active issues before creating new ones.
- Apply explicit labels first; add project or domain labels only when requested or clearly useful.

8. Return the execution handoff.
- Include resolved context, created issues, dependency map, and assumptions.
- If the tickets form a coordinated generated-artifact batch, say so directly and recommend a single integration PR after parallel ticket execution.
- If implementation is next, say `Use $execute-linear-ticket for implementation.`

## Output

- `Resolved Context`: issue source or cwd-derived context, team, project
- `Plan Source`: `Superpowers` or `Fallback template`
- `Created Issues`: key, title, URL, labels
- `Execution Plan`: `Can Start Now` and `Blocked`
- `Integration Strategy`: `One PR per ticket` or `Single integration PR after parallel execution`
- `Delivery Posture`: execution posture and expected sensors by issue
- `Compound Notes`: likely learning, skill, or follow-up sensor opportunities
- `Assumptions`: scope splits, label mappings, or context clarifications

## Guardrails

- Optimize for minimum dependency chains and maximum parallel execution.
- Do not create tickets directly from vague scope; if the work is not concrete enough to become a checklist, plan first.
- Do not create umbrella tickets when the plan yields separable implementation outcomes.
- Avoid implementation detail that does not help define the ticket.
- Treat repo and agent documentation refresh as required follow-up work when a feature changes capabilities, workflows, architecture, or other durable repo behavior; keep the repo honest about its standing behavior.
- Prefer a single integration PR when separate ticket PRs would mostly fight over regenerated artifacts rather than represent meaningful review boundaries.
- Do not make Linear tickets into implementation scripts. Capture outcomes, tests, sensors, posture, and boundaries; let execution skills choose the detailed path.
- Do not let a ticket omit tests for behavior-bearing work unless the posture explains why characterization or sensor-only validation is more appropriate.
- Ticket creation is done when Linear is up to date and the next execution order is obvious.
