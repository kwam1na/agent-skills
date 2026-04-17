---
name: create-linear-ticket
description: Use when you need to turn planned work into atomic, parallelizable Linear tickets for any team or project.
---

# Creating Linear Tickets

Use this skill to turn approved work into concrete Linear issues that are atomic, parallelizable, and testable across different teams and projects.

If the tickets already exist and the user wants implementation instead of ticket creation, do not keep using this skill for execution. Hand off to `$execute-linear-ticket`.

## When to Use

- The user asks to create tickets from active work.
- The user wants planning first, then ticket creation.
- The user wants issues structured for parallel execution in Linear.

Do not use this skill when:
- the relevant tickets already exist
- the user is asking to implement or continue implementation
- the main problem is execution hygiene rather than ticket creation

## Project Resolution Contract

- If a Linear issue ID is present, call `get_issue` first and derive the team/project from the issue.
- If no issue id is present, look for explicit project/team names in the prompt and validate them with `get_project` and `get_team`.
- If neither source exists, stop and ask the user for project context.
- If the issue exists but has no project, stop and ask instead of silently falling back.
- If the named or derived project does not exist in Linear, stop and surface the mismatch clearly.
- If the user supplies both an issue id and explicit project/team names and they conflict, surface the mismatch instead of guessing.

## Workflow

0. Confirm this is ticket creation work, not ticket execution work.
- If the tickets already exist, stop here and use `$execute-linear-ticket`.
- If the user is mixing both intents, do ticket creation first, then explicitly hand execution off to `$execute-linear-ticket`.

1. Resolve project context before planning the tickets.
- Follow the project resolution contract above.
- Treat the resolved team/project as required context for all later Linear mutations.
- Report the resolved team/project in the final handoff.

2. Generate a plan first.
- Use `superpowers:writing-plans` as the primary planning step when Superpowers is available.
- If `superpowers:writing-plans` is unavailable, use `references/atomic-plan-template.md` to generate the plan manually.
- Plan output should be a checklist of concrete implementation tasks.

3. Normalize plan items into ticket candidates.
- Convert each actionable checklist item into one ticket candidate.
- Merge items only when they are inseparable in implementation and validation.
- Split items when they can be shipped and tested independently.

4. Enforce atomicity and parallelizability.
- Each ticket must have one shippable outcome.
- Each ticket should be independently mergeable and testable.
- Capture dependency edges only when a ticket is truly blocked.

5. Build deterministic ticket bodies.
- Use `references/atomic-ticket-template.md`.
- Include `Scope`, `Acceptance Criteria`, and `Test Scenarios` in every ticket.
- Include security, authorization, or idempotency scenarios only when the work actually touches those areas.

6. Execute in Linear.
- First verify connectivity and access to the resolved workspace context.
- Prefer direct Linear MCP operations.
- If mutation fails in-session, use the CLI fallback documented in `references/linear-execution-notes.md`.
- Apply explicit labels first. Add project-specific labels only when the user requested them or the touched code area makes them obviously relevant.

7. Return complete handoff.
- Issue key, URL, title, labels.
- Short execution map: `can start now` vs `blocked by`.
- Resolved team/project context.
- Any assumptions, including scope decisions or label mappings.
- If implementation is the next likely step, explicitly say that `$execute-linear-ticket` is the correct follow-on skill.

## Quality Rules

- Do not force frontend/backend splits unless they arise naturally from the plan.
- Optimize for minimum dependency chains and maximum parallel execution.
- Avoid duplicate tickets by checking for near-identical active issues before creation.
- Do not drift into implementation updates, branch strategy, or PR commentary beyond what is needed to define the tickets.
- Make the execution boundary explicit: ticket creation ends when Linear is up to date and the next implementation order is clear.

## Output Format

1. `Resolved Context`
- `Issue Source`: issue id, explicit project/team, or user-supplied clarification
- `Team`: resolved Linear team
- `Project`: resolved Linear project

2. `Plan Source`
- `Superpowers` or `Fallback template`

3. `Created Issues`
- `<KEY>` - `<title>` - `<url>` - `labels: ...`

4. `Execution Plan`
- `Can Start Now`: list of parallel tickets.
- `Blocked`: ticket -> blocking ticket(s).

5. `Next Step`
- If execution should follow immediately, say: `Use $execute-linear-ticket for implementation.`

6. `Assumptions`
- Scope choices, label mappings, or project-context clarifications.
