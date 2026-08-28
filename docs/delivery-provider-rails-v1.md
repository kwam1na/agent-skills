# Delivery Provider Rails Contract, Version 1

This contract defines a neutral boundary between a delivery consumer and one
delivery provider. It specifies only the documents exchanged at that boundary
and the state rules for one delivery attempt.

The sole contract version is `delivery-provider-rails/1`. A consumer first
sends `negotiate` with the versions it accepts. A provider returns
`negotiation`: `supported` selects this version, while `unsupported` selects
`null`. No request is accepted before successful negotiation.

## Messages

A `request` carries a stable `requestId`, an `idempotencyKey`, and an opaque
object `payload`. An accepted attempt may emit zero or more ordered `progress`,
`evidence`, and `blocker` events, followed by exactly one `terminal` event.
Terminal outcomes are `success`, `blocked`, `failed`, `cancelled`, and
`indeterminate`.

`progress` reports a non-terminal checkpoint. `evidence` names retained proof.
`blocker` names a current constraint; it is not terminal on its own. A terminal
`blocked` outcome closes the attempt.

A `cancel` carries a stable `cancellationId`. Repeating the same cancellation is
idempotent. Once the provider accepts cancellation for an active attempt, that
attempt may terminate only as `cancelled` or `indeterminate`.

## Validation and unknown fields

Every message is validated as a complete document before state changes occur.
Unknown envelope fields are malformed and cause the whole message to be
rejected. Missing, mistyped, or out-of-version fields are also malformed. The
objects held by `payload`, `details`, and `result` are explicitly opaque to this
contract; their nested fields are owned by the adopter.

## Ordering, duplication, and finality

Event sequence numbers start at one and increase by one. An exact repeat at an
already accepted sequence is a duplicate and is ignored. A conflicting message
at the same sequence is malformed.

Repeating an identical request with the same request and idempotency identities
does not execute it again. Reusing those identities with changed content is
malformed.

Terminal outcomes are absorbing. Any later event for the same attempt is
rejected and cannot reopen or replace its outcome.

Cancellation cannot become success: once cancellation is accepted, a later
success is invalid.

Interruption becomes indeterminate when a consumer loses the event sequence
before a terminal event. The consumer closes that attempt locally as
`indeterminate`; a later success for the same attempt is rejected. A new attempt
requires a new `requestId` and `idempotencyKey`.

## Conformance

The shared vectors in `tests/fixtures/delivery-provider-rails-v1.json` exercise
provider inputs and consumer outputs independently. They cover supported and
unsupported negotiation, closed-envelope malformation, request and event
duplicates, interruption, cancellation, every terminal outcome, and the rule
that cancelled or indeterminate attempts never become successful.
