---
name: ticket-retrospective
description: Use when ticket delivery handoff is complete and you need a strict, redacted post-handoff report from session and runtime logs for skill-improvement feedback loops.
---

# Ticket Retrospective

## Overview

Run this skill manually after ticket handoff when you want a strict retrospective artifact for process tuning. It analyzes run logs, extracts inconsistencies, struggles, and resolutions, and writes a deterministic markdown report for later skill tuning.

This skill is post-delivery analysis only. It must not block delivery handoff if report generation fails.

## When to Use

- A ticket has been delivered and handed off for review.
- PR is non-draft, required checks are green, and Linear is in `In Review`.
- You need a redacted markdown artifact for downstream skill improvement.

Do not use this skill when:
- the ticket is still in active implementation
- delivery gates are still red/pending
- the task requires browser automation (Playwright is out of scope)

## Invocation Contract

Required args:
- `ticket_id`
- `thread_id`
- `repo_path`
- `branch`
- `pr_url`
- `linear_issue_id`
- `start_ts`
- `handoff_ts`

Optional args:
- `commit_sha`
- `validation_summary`
- `ci_check_ids`

Delivery gate flags (default `true`):
- `pr_non_draft`
- `required_checks_green`
- `linear_in_review`

## Workflow

1. Confirm handoff gates.
- Only run this skill after delivery gates pass.
- If any gate is false, either defer the retrospective or run it with explicit warning flags.

2. Run the generator script.
- Script: `scripts/generate_retrospective.py`
- Source policy:
  - Session JSONL (`$CODEX_HOME/sessions/**/*.jsonl`, archived fallback)
  - SQLite logs (`$CODEX_HOME/logs_2.sqlite`)

3. Keep it non-blocking.
- If generation fails, the script writes:
  - alert artifact under `ticket-retrospectives/alerts/`
  - queue item in `ticket-retrospectives/queue.jsonl`
- Do not block ticket handoff on retrospective failures.

4. Publish report metadata.
- Report path pattern:
  - `$CODEX_HOME/ticket-retrospectives/YYYY/MM/<ticket-id>/<handoff-ts>-<thread-id>.md`
- Index path:
  - `$CODEX_HOME/ticket-retrospectives/index.jsonl`
- Index updates are idempotent by `run_key = ticket_id:thread_id:handoff_ts`.

## Command

```bash
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 "$CODEX_HOME/skills/ticket-retrospective/scripts/generate_retrospective.py" \
  --ticket-id "V26-202" \
  --thread-id "019d7ee5-5b59-7dc1-8406-356f3db72acc" \
  --repo-path "/Users/kwamina/athena/packages" \
  --branch "codex/V26-202-ticket-handoff" \
  --pr-url "https://github.com/kwam1na/athena/pull/1234" \
  --linear-issue-id "V26-202" \
  --start-ts "2026-04-12T01:00:00Z" \
  --handoff-ts "2026-04-12T03:15:00Z" \
  --commit-sha "abc1234" \
  --validation-summary "bun run check ✅" \
  --ci-check-ids "build,lint,test"
```

## Report Contract

The report always includes:
- `Metadata`
- `Delivery Gate Check`
- `Timeline Summary`
- `Inconsistencies Found`
- `Struggles and Resolutions`
- `Reusable Heuristics`
- `Proposed Skill Deltas`
- `Light Metrics`
- `Redacted Evidence Appendix`

Frontmatter keys:
- `ticket_id`
- `thread_id`
- `created_at`
- `skill_version`
- `repo`
- `branch`
- `pr_url`
- `status`

See `references/report-contract.md` for strict section semantics.

## Validation

Run local tests before updating this skill:

```bash
cd "$CODEX_HOME/skills/ticket-retrospective/scripts"
python3 -m unittest -v test_generate_retrospective.py
```
