# Atomic Ticket Template

## Summary
One paragraph describing the problem and target outcome.

## Scope
1. Concrete implementation change.
2. Data, contract, or authorization behavior impacted.
3. Out-of-scope boundary to keep the ticket atomic.

## Acceptance Criteria
1. Primary success condition.
2. Failure-path or validation condition.
3. State-integrity or security condition when relevant.

## Test Scenarios
1. Happy path.
2. Invalid or tampered input path.
3. Authorization or ownership path when relevant.
4. Replay or idempotency path when events or webhooks are involved.

## Execution Posture
One of:
- `test-first` for new behavior or bug fixes with a clear expected outcome.
- `characterization-first` for legacy, unclear, or fragile behavior that must be captured before changing.
- `sensor-only` for pure docs, generated artifacts, configuration, or mechanical changes with no behavior.

## Expected Sensors
1. Targeted test or characterization command.
2. Broader suite, typecheck, build, lint, harness/review command, runtime scenario, or CI-equivalent command.
3. Repair path for deterministic drift, if known.

## Compounding Opportunity
Name likely reusable learning, missing sensor, reviewer gap, skill update, or `None expected`.

## Dependencies
1. Blocking ticket(s), if any.
2. Shared contracts that must be finalized first.

## Labels
1. Project labels only when the resolved project already uses them.
2. Domain labels only when they add useful routing or reporting value.
