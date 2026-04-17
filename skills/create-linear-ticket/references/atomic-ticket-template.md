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

## Dependencies
1. Blocking ticket(s), if any.
2. Shared contracts that must be finalized first.

## Labels
1. Project labels only when the resolved project already uses them.
2. Domain labels only when they add useful routing or reporting value.
