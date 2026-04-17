---
name: execute-linear-ticket
description: Use when implementing one or more existing Linear tickets end-to-end across different teams or projects.
---

# Executing Linear Tickets

Use this skill when the work already exists in Linear and the user wants execution, not fresh planning. The goal is to turn a ticket into a reliable delivery loop: pick it up, implement it on a clean branch, make reasonable scope decisions, document those decisions in Linear, and finish with validation plus a clear PR or handoff state.

This is the execution companion to `$create-linear-ticket`. Use that skill to create or restructure tickets. Use this skill to carry existing tickets through implementation.

## When to Use

- The user asks to work on a specific Linear issue.
- The user asks to proceed with outstanding Linear work already tracked in tickets.
- The user expects Codex to choose the next sensible ticket from a known backlog or execution plan.
- The work spans implementation plus ticket hygiene: status updates, comments, PR links, or follow-up tickets for deferred scope.

Do not use this skill when:
- the user is still deciding scope and wants planning first
- the work is not tracked in Linear yet
- the task is unrelated to a Linear workflow

## Project Resolution Contract

- If a Linear issue ID is present, call `get_issue` first and derive the team/project from the issue.
- If no issue id is present, look for explicit project/team names in the prompt and validate them with `get_project` and `get_team`.
- If neither source exists, stop and ask the user for project context.
- If the issue exists but has no project, stop and ask instead of silently falling back.
- If the named or derived project does not exist in Linear, stop and surface the mismatch clearly.
- If the user supplies both an issue id and explicit project/team names and they conflict, surface the mismatch instead of guessing.

## Defaults

- Start each new implementation branch from the latest `origin/main`
- Use a fresh `codex/` branch and isolated worktree for each ticket
- Prefix every commit message with the Linear ticket id
- Prefer direct Linear MCP operations for status changes, comments, and follow-up issue creation
- Execution mode default: `auto_review_and_merge = on` (opt out only when the user explicitly requests it)
- Auto-merge target branch: `main`
- Auto-merge method: `squash`
- Review loop cap: `max_review_iterations = 3`
- Internal review gate source: `$requesting-code-review`
- Human approval is not a required merge gate unless the user explicitly asks for it
- CI merge gate: all PR checks must be green before merge

## Workflow

### 1. Pick Up The Ticket

- Read the Linear issue first.
- Resolve project context before implementation.
- Capture:
  - title
  - scope
  - acceptance criteria
  - test scenarios
  - milestone, labels, and parent issue when relevant
- Move the issue to `In Progress` when work begins.
- If the user asked for a series of tickets, choose the next one based on explicit dependencies first, then implementation leverage.

### 2. Build An Isolated Execution Workspace

- Create a fresh worktree from the latest `origin/main`.
- Use a branch name with the `codex/` prefix and the ticket id in the name.
- Keep the same ticket id in every commit message so branch history stays traceable back to the Linear issue.
- Do not branch from a stale local `main` if the repo already moved on.
- Respect repo rules in `AGENTS.md` and related docs before coding.

### 3. Reproduce And Understand Before Fixing

- Reproduce the failure, warning, or missing behavior before editing code.
- Read the exact files implicated by the ticket.
- If the issue is ambiguous, confirm the real code path locally instead of trusting memory or ticket phrasing.
- Prefer the smallest accurate root-cause statement you can defend.

### 4. Execute Autonomously

- Default to implementing the fix end-to-end inside the current turn.
- Make reasonable scope decisions without stopping the user unless the consequences are materially different.
- Keep changes aligned to the ticket's outcome. Avoid bundling unrelated cleanup just because you are nearby.
- If a related improvement is necessary to make the fix coherent, include it and explain why.

### 5. Make Judgment Calls Deliberately

When you make a non-obvious call, document it both in your own summary and in Linear.

Typical judgment calls:
- splitting follow-up work into a new ticket instead of broadening the current one
- keeping a ticket scoped to one subsystem even when adjacent code is tempting to clean up
- preserving a runtime boundary because it appears intentional
- choosing a library-supported composition pattern over inventing a custom helper layer
- separating unrelated drift from the primary ticket

For each significant judgment call, record:
- what you decided
- why you decided it
- what you intentionally left out

### 6. Keep Linear In Sync While You Work

Update Linear as part of execution, not only at the end.

Minimum expectations:
- move the issue to `In Progress` when work starts
- add a comment when there is meaningful implementation progress
- add a comment when you open the PR
- move the issue to `In Review` once implementation is complete, the PR is open, and the ticket is primarily waiting on review or merge
- include branch name, commit or HEAD when useful, validation run, and major scope decisions
- when auto-review or merge is active, include telemetry fields in loop comments:
  - `review_iteration`
  - `internal_review_decision`
  - `critical_count`
  - `important_count`
  - `github_feedback_blockers`
  - `all_pr_checks_green`
  - `merge_status`
  - `merge_commit_sha` (when merged)

If you defer work discovered during implementation:
- create a follow-up issue instead of burying it in prose
- put it in the correct milestone or state
- link it back to the umbrella or parent issue when appropriate
- explain the split in a Linear comment

### 7. Validate Before Claiming Success

Run the smallest targeted tests first, then the broader package or project checks.

Typical validation ladder:
- focused failing or regression test
- package test suite
- typecheck, build, and lint checks relevant to the touched code
- project-specific preflight commands if the repo defines them
- `git diff --check`

If a validation step is blocked or hangs:
- say exactly what happened
- note whether it appears unrelated or introduced by your change
- do not quietly omit it

### 8. Prepare The PR Correctly

Before opening or updating a PR:
- sync the branch with the latest `origin/main`
- rerun the relevant validation checks after syncing
- do not open or update the PR until the required local validations pass
- when `auto_review_and_merge = on`, open or keep the PR as draft during review or fix loops

PR conventions:
- title format: `[<TICKET-ID>]: Title`
- body must contain:
  - `## Summary`
  - `## Why`
  - `## Validation`
- include the Linear ticket link at the end of the PR body

After opening the PR:
- push the branch
- add the PR link to the Linear ticket
- move the Linear issue to `In Review` once the work is in PR review or merge loop
- note final validation status and major scope decisions in Linear

### 9. Run Deterministic Review + Merge Loop

When `auto_review_and_merge = on`, run this loop with `max_review_iterations = 3`.

For each iteration `N`:

1. Run `$requesting-code-review`.
2. Parse required merge gate output:
   - `decision: APPROVED | CHANGES_REQUESTED`
   - `critical_count: <int>`
   - `important_count: <int>`
   - `minor_count: <int>`
3. Treat internal review as blocking when:
   - `decision = CHANGES_REQUESTED`, or
   - `critical_count > 0`, or
   - `important_count > 0`.
4. Check external GitHub feedback blockers:
   - PR review state `CHANGES_REQUESTED`
   - unresolved actionable PR review threads or comments
5. Check CI merge gate:
   - all PR checks must be green
6. If any blocker exists:
   - fix issues
   - rerun relevant validations
   - push updates
   - rerun `$requesting-code-review`
   - recheck GitHub review blockers and all PR checks
7. If no blockers remain:
   - mark PR ready for review (if draft)
   - merge with squash into `main`
   - record merge SHA and PR URL

### 10. Close The Loop and Handle Fallbacks

Post-merge success path (`auto_review_and_merge = on`):
- move Linear issue from `In Review` to `Done`
- post merge comment with:
  - PR URL
  - merge SHA
  - final telemetry fields

Fallback path A: loop cap reached without gate pass:
- stop auto-merge attempts
- leave issue in `In Review`
- post unresolved-item checklist in Linear and user handoff
- include exact blockers and latest telemetry values

Fallback path B: merge permission or settings prevent merge:
- leave issue in `In Review`
- post exact merge blocker text in Linear and user handoff
- include what passed vs what still blocks

Opt-out mode (`auto_review_and_merge = off`):
- do not auto-merge
- complete handoff at review-ready state with explicit "awaiting manual merge or review" note

## Comment Template

Use concise Linear comments with this shape when helpful:

```md
Implementation update:

- Branch: `codex/ABC-123-short-name`
- Current commit: `abc1234`

What changed:
- short concrete outcome
- short concrete outcome

Validation:
- `npm test -- some-target` ✅
- `tsc --noEmit` ✅

Review or merge telemetry:
- `review_iteration`: `2`
- `internal_review_decision`: `APPROVED`
- `critical_count`: `0`
- `important_count`: `0`
- `github_feedback_blockers`: `[]`
- `all_pr_checks_green`: `true`
- `merge_status`: `merged`
- `merge_commit_sha`: `abc123def456`

Judgment call:
- explain the scoped decision and why
```

## Common Mistakes

- Starting from a stale branch after a prior checkpoint merged.
- Treating ticket execution as code-only work and forgetting Linear status or comments.
- Leaving the issue in `In Progress` after the work is effectively handed off for review.
- Expanding ticket scope silently instead of recording a judgment call.
- Discovering follow-up work and leaving it undocumented.
- Opening a PR without rebasing onto the latest `origin/main`.
- Skipping `$requesting-code-review` in auto-review mode.
- Ignoring GitHub `CHANGES_REQUESTED` or unresolved actionable review threads or comments.
- Creating commits whose messages omit the Linear ticket id.
- Using a PR title that omits the Linear ticket id.
- Putting the Linear link at the top of the PR body instead of the end.
- Attempting merge before all PR checks are green.
- Exceeding review loop cap without posting an unresolved-item checklist.

## Quick Reference

- Existing ticket, execution requested: use this skill
- Need to create or restructure tickets first: use `$create-linear-ticket`
- Default execution mode: `auto_review_and_merge = on`
- Deterministic loop: Execute -> Draft PR -> Internal review -> Fix or re-review -> all checks green -> squash merge -> Linear `Done`
- New branch after a merged checkpoint: always from latest `origin/main`
- CI merge gate: all PR checks green
- Commits for ticket work: include the ticket id in every commit message
- Deferred scope found during execution: create a follow-up ticket
- Project-specific validation or handoff hooks belong in a project wrapper such as `$execute-athena-linear-ticket`
