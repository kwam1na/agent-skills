# Retrospective Report Contract

This file defines strict output expectations for `generate_retrospective.py`.

## Frontmatter

Required keys:
- `ticket_id`
- `thread_id`
- `created_at`
- `skill_version`
- `repo`
- `branch`
- `pr_url`
- `status`

## Sections

1. `Metadata`
- Stable run identifiers and execution summary metadata.

2. `Delivery Gate Check`
- Gate booleans:
  - `pr_non_draft`
  - `required_checks_green`
  - `linear_in_review`

3. `Timeline Summary`
- Time window, source files, event/tool/log counts, elapsed phase metrics.

4. `Inconsistencies Found`
- Claim-vs-evidence mismatches.
- Partial/skipped gate evidence.
- State drift indicators.

5. `Struggles and Resolutions`
- `Struggles`: repeated failures, retries, aborted turns, context churn.
- `Resolutions`: evidence of successful recovery patterns.

6. `Reusable Heuristics`
- Portable execution heuristics extracted from run behavior.

7. `Proposed Skill Deltas`
- Concrete candidate updates for ticket execution skills or companion skills.

8. `Light Metrics`
- `retry_count`
- `tool_error_count`
- `test_rerun_count`
- `pivot_count`
- elapsed phase metrics

9. `Redacted Evidence Appendix`
- Minimal excerpts only.
- Secrets and user-identifying paths must be masked.
