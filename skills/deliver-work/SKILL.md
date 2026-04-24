---
name: deliver-work
description: Use when the user asks Codex to build, fix, modify, refactor, debug, or ship software work and no narrower delivery skill is already the better entrypoint.
---

# Deliver Work

## Overview

Use this as the default entrypoint for software delivery when the user has not already routed the work through a more specific skill. It turns ordinary requests into compound delivery: plan the slice, work test-first or characterization-first, validate with repo sensors, review, compound learning, and hand off clearly.

Always apply `$compound-delivery-kernel`. The kernel owns the reusable delivery contract; this skill owns routing and default behavior.

## Route First

- If the work is already tracked in Linear, use `$execute-linear-ticket`.
- If approved work needs tickets, use `$create-linear-ticket`.
- If requirements are fuzzy or product shape is unresolved, use `compound-engineering:ce-brainstorm` when available.
- If the user asks for an implementation plan or the work is multi-step with unclear sequencing, use `compound-engineering:ce-plan` when available.
- If the request is a bug with unknown root cause, use a systematic debugging skill before planning the fix.
- If the task is purely a review, use the available code-review skill instead of implementing.
- If the user explicitly names a different skill or workflow, honor that routing.

When none of those routes is a better fit, continue here.

## Delivery Defaults

1. Understand the outcome, scope boundary, and acceptance criteria.
2. Inspect repo instructions and sensors before editing: AGENTS/CLAUDE-style guidance, package docs, test commands, validation maps, harness checks, CI-equivalent commands, and runtime scenarios.
3. Choose execution posture:
   - `test-first` for new behavior and bug fixes with a clear expected outcome.
   - `characterization-first` for legacy, unclear, or fragile behavior.
   - `sensor-only` only for pure docs, generated artifacts, configuration, or mechanical changes with no behavior.
4. Implement the smallest useful slice:
   - For `test-first`, write/update the failing test and verify red before production code.
   - For `characterization-first`, capture current behavior first, then change it.
   - For `sensor-only`, name the proving sensor and why behavior tests do not apply.
5. Run the narrowest relevant sensor after each meaningful slice, then the broader merge-level sensor set before handoff.
6. Review the diff against acceptance criteria, tests, repo sensors, and project standards. Use specialized review skills or agents when risk warrants it.
7. Apply the kernel's proactive-ticket policy for evidence-backed follow-up work. Do not expand current scope silently.
8. Make the compound decision before final handoff: solution doc, skill update, follow-up ticket, missing sensor request, or no durable learning.

## Handoff

Report:

- what changed
- tests and repo sensors run
- review result and residual risk
- proactive tickets created or deferred
- compounding decision
- any blocked validation with exact cause

## Red Flags

- Starting implementation before selecting test posture.
- Using final green tests as proof that a failing test existed first.
- Treating repo docs as the workflow source instead of as sensor and context sources.
- Filing speculative tickets without concrete evidence.
- Skipping the compound decision because the code change is done.
