# Atomic Ticket Template

## Summary
One paragraph describing the problem and target outcome.

## Scope
1. Concrete implementation change.
2. Data/contract/authorization behavior impacted.
3. Out-of-scope boundary to keep the ticket atomic.

## Acceptance Criteria
1. Primary success condition.
2. Failure-path or validation condition.
3. State integrity/security condition (if relevant).

## Test Scenarios
1. Happy path.
2. Invalid/tampered input path.
3. Authorization or ownership path.
4. Replay/idempotency path (if events/webhooks are involved).

## Dependencies
1. Blocking ticket(s), if any.
2. Shared contracts that must be finalized first.

## Labels
1. Package label(s) based on touched code area.
2. Domain labels (checkout, security, etc.) as needed.
