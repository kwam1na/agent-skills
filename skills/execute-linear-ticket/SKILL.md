---
name: execute-linear-ticket
description: Use when work already exists in Linear and the user wants implementation rather than new planning.
---

# Executing Linear Tickets

Use this skill to carry an existing Linear issue through implementation, review, merge, and ticket closure. Use `$create-linear-ticket` first if the work is not yet ticketed or still needs decomposition.

## Delivery Posture

Apply `$compound-delivery-kernel` throughout execution. Linear owns the work record, the repo owns sensors, and the skills system owns the delivery workflow. Preserve test-driven delivery: behavior changes start with a failing test or characterization capture, not implementation.

## When to Use

- The user asks to work on a specific Linear issue.
- The user asks to continue a backlog or execution plan that is already tracked in Linear.
- The work includes implementation plus ticket hygiene such as status updates, comments, PR links, or follow-up issues.

Do not use this skill when:
- the work needs planning or ticket creation first
- the work is not tracked in Linear yet
- the task is unrelated to a Linear workflow

## Delivery Contract

- Default to completing the ticket autonomously end-to-end.
- Delivery means the PR is merged into `main` and the ticket is already marked `Done` in Linear by merge automation.
- When executing a coordinated batch of related tickets, delivery can mean all tickets land through one shared integration PR rather than one PR per ticket.
- Delivery also means you leave the local repo tidy and back on `main`.
- Do not stop at "PR open" or "ready for review" unless the user explicitly asked for that narrower handoff.
- Only treat something as a blocker when it genuinely requires user input.
- Document significant scope decisions in Linear as you work.

## Red Flags

- "The PR is open, so this is done."
- "I'll update Linear at the end."
- "The review loop hit `3`, so I should stop even though the next fix is obvious."
- "The checks are probably fine" or "the review comments are minor."
- "`pre-push` passed, so remote CI parity is guaranteed."
- "The sub-agent is taking too long, so I should kill it early."
- "Every harness or repo validation failure needs a manual fix."
- "The final suite passed, so test-first happened."
- "The PR merged, so there is nothing left to teach the system."
- "I noticed something adjacent, so I should silently expand this ticket."
- "A vague improvement idea deserves a proactive ticket."

## Shared Context

Apply `references/linear-project-resolution.md` before mutating Linear or choosing the next ticket from a backlog.

Use this resolution order before asking the user for context:
- If a Linear issue ID is present, call `get_issue` first and derive the team/project from the issue.
- If no issue id is present, look for explicit project/team names and validate them with `get_team` and `get_project`.
- If neither source resolves context, inspect the current working directory the skill was invoked from.
- Build candidate project names from the current directory basename, the git repo root basename when it differs, and obvious workspace metadata such as `package.json` names.
- If exactly one confident project match exists, use it automatically and report that it was cwd-derived.
- If the issue exists but has no project, stop and ask instead of silently falling back.
- If the named or derived project does not exist in Linear, stop and surface the mismatch clearly.

## Defaults

- Start each ticket from the latest `origin/main` in a fresh worktree and `codex/` branch.
- Include the Linear ticket id in every commit message.
- Prefer Linear MCP operations for status changes, comments, and follow-up issue creation.
- Prefer sub-agents when the work can be split into parallelizable chunks with clear ownership.
- If several tickets share high-conflict generated artifacts, prefer isolated worktrees per ticket plus one later integration branch instead of opening multiple artifact-conflicting PRs early.
- Prefer the repo's PR-equivalent validation command when one exists; do not assume local `pre-push` hooks cover the full remote CI surface.
- Default execution posture is `test-first` for new behavior and bug fixes, `characterization-first` for unclear legacy behavior, and `sensor-only` only for pure docs, generated artifacts, configuration, or mechanical changes with no behavior.
- `auto_review_and_merge = on` unless the user opts out.
- Merge target `main`; merge method `squash`; review loop cap `3`.
- Human approval is not required unless the user explicitly asks for it.
- All PR checks must be green before merge.

## Workflow

### 1. Pick Up The Issue

- Read the Linear issue first.
- Capture title, scope, acceptance criteria, test scenarios, and any relevant labels, milestone, or parent links.
- Capture execution posture, expected repo sensors, and compounding opportunity when present; infer them from the ticket and repo context when absent.
- Move the issue to `In Progress` when work begins.
- If the user asked to continue through a backlog, choose the next ticket by explicit dependencies first, then implementation leverage.

### 2. Prepare An Isolated Workspace

- Create a fresh worktree from the latest `origin/main`.
- Use a `codex/` branch name that includes the ticket id.
- Respect repo rules in `AGENTS.md` and related docs before coding.
- If the overall batch is expected to land through one integration PR, still keep each ticket's implementation isolated in its own worktree or branch so the final integration step is deliberate.

### 3. Reproduce And Understand Before Editing

- Reproduce the failure, warning, or missing behavior before editing code.
- Read the exact files implicated by the ticket.
- If the ticket touches UI or other user-facing surfaces, invoke `$designing-frontends` before shaping the UI implementation.
- Also invoke `$frontend-skill` before shaping the UI implementation.
- Prefer the smallest accurate root-cause statement you can defend.
- Discover the closest existing tests or characterization fixtures before editing behavior.

### 4. Implement With Scope Discipline

- For `test-first` work, write or update the failing test first, run it, and confirm the failure proves the intended behavior before implementation.
- For `characterization-first` work, capture current behavior with a test or fixture before changing it, then add the intended-behavior test when the desired outcome is clear.
- For `sensor-only` work, identify the sensor that proves the mechanical change and record why no behavior test is appropriate.
- Keep changes aligned to the ticket's outcome. Avoid bundling unrelated cleanup just because you are nearby.
- Make reasonable scope decisions without pausing the user unless the consequences materially change the intended outcome.
- When a non-obvious call matters, record what you decided, why, and what you intentionally left out.
- If new work is discovered but should not expand the current ticket, apply `$compound-delivery-kernel` proactive-ticket rules: create a follow-up issue only when the work is concrete, scoped, evidence-backed, and separate from current acceptance criteria; link it in Linear and the PR/handoff.
- When sub-agents are feasible, give them narrow scope and disjoint ownership so work can proceed in parallel safely.
- Do not interrupt, close, or give up on a sub-agent just because it is taking time; let it use its allotted time unless the task is blocked, mis-scoped, conflicting with higher-priority work, or you need to redirect it with better context.

### 5. Keep Linear Current

- Add comments when there is meaningful progress, when the PR opens, and when the ticket state changes.
- Move the issue to `In Review` once implementation is complete, the PR is open, and the ticket is primarily waiting on review or merge.
- For coordinated batches, comment on each issue when its branch is ready for integration, then link the final shared PR on every ticket once opened.
- Include these fields when relevant:
  - branch name
  - current commit or HEAD
  - execution posture
  - validation run
  - major scope decisions
  - compounding decision or pending learning
- When auto-review or merge is active, also include:
  - `review_iteration`
  - `internal_review_decision`
  - `critical_count`
  - `important_count`
  - `github_feedback_blockers`
  - `all_pr_checks_green`
  - `merge_status`
  - `merge_commit_sha` when merged

### 6. Validate Before Claiming Success

- Run the smallest targeted test first, then the relevant suite, typecheck, build, lint, repo preflight, and `git diff --check`.
- Match validation to the ticket's expected sensors and supplement with discovered repo sensors when the ticket is incomplete.
- If the repo defines a PR-equivalent command, run that before trusting local parity with remote CI.
- When harness or repo validation fails, first classify it as deterministic repairable drift or a semantic blocker.
- If the repo already defines a canonical repair command for deterministic drift, run that repair once, rerun the blocked validation once, and continue only if the rerun passes.
- Do not invent self-corrections for semantic failures; investigate those normally.
- If bounded self-repair refreshed tracked artifacts, review and commit those repaired files before pushing again.
- If a validation step hangs or is blocked, say exactly what happened and whether it appears related to your change.

### 7. Open Or Update The PR Correctly

- sync the branch with the latest `origin/main`
- rerun the relevant validation checks after syncing
- do not open or update the PR until the required local validations pass
- when `auto_review_and_merge = on`, open or keep the PR as draft during review or fix loops
- if multiple tickets were intentionally batched because of shared generated artifacts, open a single integration PR from a branch that contains the combined work plus one fresh regeneration of the shared artifacts

PR conventions:
- title format: `[<TICKET-ID>]: Title`
- body must contain:
  - `## Summary`
  - `## Why`
  - `## Validation`
- include the Linear ticket link at the end of the PR body

For coordinated integration PRs:
- use a combined title that includes all ticket ids or a clear batch label
- list every included ticket in the PR body
- make clear that the shared PR exists to avoid repeated merge conflicts in generated artifacts while preserving ticket-level scope in Linear

After opening the PR:
- push the branch
- add the PR link to the Linear ticket
- if it is a coordinated integration PR, add the same PR link to every included ticket
- note final validation status and major scope decisions in Linear

### 8. Run The Review + Merge Loop

- Run `$requesting-code-review`.
- Treat any of the following as blocking:
  - internal review `decision = CHANGES_REQUESTED`
  - `critical_count > 0`
  - `important_count > 0`
  - GitHub review state `CHANGES_REQUESTED`
  - unresolved actionable PR review threads or comments
  - any PR check not green
- If blocked, fix the issue, rerun the relevant validations, push, rerun `$requesting-code-review`, and recheck GitHub feedback plus CI.
- If remote GitHub Actions fails after local validation passed, inspect the failing logs and deduce the concrete root cause instead of guessing from the check name alone.
- Treat remote-only failures as a local parity or harness gap until disproven:
  - if the failure belongs to the current ticket, fix it in the current ticket
  - if the failure exposed missing local guardrails, create a follow-up Linear issue scoped to the missing parity or harness remediation with source evidence from the failing check
  - if both are true, do both
- The follow-up issue should capture the failing remote check, the local validations that passed, the root cause, and the local command, harness mapping, or coverage addition needed so the failure is caught before CI next time.
- Link that follow-up issue from the current Linear ticket and the PR comment trail when it materially affects the handoff.
- Iterate up to `3` times by default, but continue autonomously if the next fix is still clear.
- When all gates pass, mark the PR ready if needed and squash-merge into `main`.

### 9. Compound The Learning

- Before final ticket closure, decide whether the work taught the system something reusable.
- Use `$ce-compound` or an equivalent solution-doc workflow when the repo has a `docs/solutions/` knowledge base and the learning is repo-specific.
- Update a skill when the learning changes how agents should deliver work across repos.
- Create a follow-up Linear issue when the learning is a concrete missing repo sensor, missing validation map coverage, missing reviewer, or tooling gap that should be implemented later; include the source evidence and why it is separate from the current ticket.
- Record `No durable learning` only when the change is local, obvious, and unlikely to recur.
- Include the compounding decision in the final Linear comment and handoff.

### 10. Close The Loop

- After merge, confirm the ticket has already moved to `Done` via merge automation.
- If merge succeeded but Linear did not move to `Done`, update the ticket directly or document the automation mismatch in Linear before handoff.
- Post a final Linear comment with the PR URL, merge SHA, final telemetry, validation evidence, and compounding decision.
- For coordinated batches, confirm every included ticket reached `Done`, not just the ticket that happened to anchor the PR title.
- After delivery, align the local `main` branch with `origin/main`, switch back to `main`, and confirm the local checkout reflects the merged result.
- Clean up the working tree and any temporary worktree or branch created for the ticket so the local repo is tidy before handoff.
- If repeated blockers remain and the next fix is not clear, leave the issue in the most accurate state, post an unresolved-item checklist with the latest telemetry, and hand off the exact blocker.
- If merge permissions or repo settings prevent merge, leave the ticket in `In Review` and document the exact blocker.
- If `auto_review_and_merge = off`, stop at review-ready state and say it is awaiting manual review or merge.
