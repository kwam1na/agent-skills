---
name: execute-athena-linear-ticket
description: Use when implementing Athena Linear tickets and you need Athena-specific validation, handoff, and retrospective hooks.
---

# Executing Athena Linear Tickets

## Overview

Use `$execute-linear-ticket` as the generic execution core. This wrapper keeps Athena defaults plus Athena-only validation, handoff video, and retrospective requirements on top of that generic Linear execution flow.

This is the Athena execution companion to `$create-athena-linear-ticket`. Use that skill to create or restructure Athena tickets. Use this skill to carry existing Athena tickets through implementation.

## When to Use

- The user says to work on a specific Athena Linear issue such as `V26-174`.
- The user asks to "proceed" with outstanding Athena Linear work already tracked in tickets.
- The user expects Codex to choose the next sensible ticket from a known Athena backlog or execution plan.
- The work spans implementation plus ticket hygiene: status updates, comments, PR links, or follow-up tickets for deferred scope.

Do not use this skill when:
- the user is still deciding scope and wants planning first
- the work is not tracked in Linear yet
- the task is unrelated to Athena's Linear workflow
- the ticket already resolves to a non-Athena project

## Athena Defaults

- Apply Athena defaults only when the user did not provide project context.
- Team: `yaegars` / `V26`
- Project: `athena`
- Start each new implementation branch from the latest `origin/main`
- Use a fresh `codex/` branch and isolated worktree for each ticket
- Prefix every commit message with the Linear ticket id, for example `V26-123: tighten billing retry guard`
- Athena PR preflight command: `bun run pr:athena`
- Execution mode default: `auto_review_and_merge = on` (opt out only when user explicitly requests it)
- Auto-merge target branch: `main`
- Auto-merge method: `squash`
- Review loop cap: `max_review_iterations = 3`
- Internal review gate source: `$requesting-code-review` (AI reviewer gate only)
- Human approval is not a required merge gate unless the user explicitly asks for it
- CI merge gate: all PR checks must be green (required and non-required)
- Mandatory handoff gate: `handoff_video_recorded_and_presented = true`
- Required handoff video evidence fields: `video_scenario`, `video_command`, `video_artifact_path`
- Post-delivery retrospective companion: `$athena-ticket-retrospective`

## Wrapper Behavior

1. Start with `$execute-linear-ticket`.
- Follow the generic ticket pickup, project resolution, Linear sync, validation, PR, and review or merge flow there.

2. Apply Athena defaults only when the generic resolver does not already have context.
- If the prompt already includes a Linear issue id, explicit project name, or explicit team name, let the generic resolver validate and use that context.
- Only inject `V26` / `athena` when the generic resolver would otherwise have to stop and ask for project context.

3. Respect non-Athena context when it is explicit.
- If the supplied or resolved context points to a different project, stop using this wrapper and continue with `$execute-linear-ticket` instead of overriding the project.

## Athena-Only Hooks

Apply these rules in addition to the generic execution flow:

- Run `bun run pr:athena` after syncing with `origin/main` and before opening or updating the PR.
- Treat `bun run pr:athena` as the canonical local preflight for Athena PR readiness.
- Require recorded handoff video evidence with fields:
  - `video_scenario`
  - `video_command`
  - `video_artifact_path`
- Run `bun run harness:behavior --scenario <selected-scenario> --record-video` before handoff completion.
- After mode-specific handoff gates pass, run `$athena-ticket-retrospective` with the required context fields.

## Athena Video Scenario Selection

1. If the Linear ticket explicitly names a runtime behavior scenario, use that scenario.
2. Otherwise select from touched-surface mappings in this order:
   - storefront checkout or auth surfaces -> `storefront-checkout-bootstrap`
   - athena convex composition or auth-route surfaces -> `athena-convex-storefront-composition`
   - failure, visibility, or error-signal surfaces -> `athena-convex-storefront-failure-visibility`
   - athena admin shell or runtime route surfaces -> `athena-admin-shell-boot`
3. If nothing maps, fallback to `sample-runtime-smoke`.

## Athena Retrospective Hook

Run this after handoff gates pass. It must stay non-blocking.

```bash
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 "$CODEX_HOME/skills/athena-ticket-retrospective/scripts/generate_retrospective.py" \
  --ticket-id "V26-123" \
  --thread-id "$THREAD_ID" \
  --repo-path "$(pwd)" \
  --branch "codex/V26-123-short-name" \
  --pr-url "https://github.com/kwam1na/athena/pull/1234" \
  --linear-issue-id "V26-123" \
  --start-ts "$START_TS" \
  --handoff-ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --commit-sha "$(git rev-parse HEAD)" \
  --validation-summary "bun run pr:athena ✅" \
  --ci-check-ids "build,lint,test" || true
```

## Quick Reference

- Existing Athena ticket, execution requested: use this wrapper
- Existing non-Athena ticket, execution requested: use `$execute-linear-ticket`
- Athena defaults are fallback context only, not overrides
- Athena PR gate: run `bun run pr:athena`
- Athena handoff requires recorded video evidence
- Athena post-handoff retrospective: `$athena-ticket-retrospective`
