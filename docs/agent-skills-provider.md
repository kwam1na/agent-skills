# Agent-skills delivery provider

The agent-skills provider is a small adapter over the published
`delivery-provider-rails/1` boundary. It does not select or run agents, own a
tracker client, or replace repository delivery policy. It accepts host-owned
workflow results, verifies that the requested workflow belongs to the current
installed release, reduces review results through `review-work`, and emits
ordered progress, evidence, blocker, and terminal documents.

Run an installed provider as a line-delimited JSON process:

```sh
python -m agent_skills.provider --root /path/to/adopting-repository
```

The consumer first sends `negotiate`, then a rail `request`. The opaque request
payload retains the consumer-owned gate, candidate, provider, attempt, and run
root fields. Its `agentSkills` object names:

- the exact active release identity and installed workflow id;
- host workflow results and their prepared tree identities;
- typed findings and the informational review timestamp;
- optional host events already produced by native tools.

The release identity must match all four fields in `.agent-skills/active.json`,
and the workflow file digest is read from that verified receipt. A mismatch is
a typed blocker before review evidence can be emitted.

## Host event mapping

The adapter preserves the configured host-native operation surface without
owning its transport:

| Host operation | Rail mapping |
| --- | --- |
| `create` | progress, evidence, blocker, or failure |
| `read` | progress, evidence, blocker, or failure |
| `update` | progress, evidence, blocker, or failure |
| `search` | progress, evidence, blocker, or failure |
| `relations` | progress, evidence, blocker, or failure |
| `reconciliation` | progress, evidence, blocker, or failure |

Host references become deterministic opaque digests. Raw host payloads,
actions, credentials, idempotency keys, and transport details are never copied
into retained events or evidence. A host blocker or failure prevents success.
The provider neither retries mutations nor performs reconciliation itself.

## Review evidence

An aligned `review-work` result bound to the requested final tree produces one
deterministic `delivery-evidence/1` manifest with a `review.green/1` claim. Each
selected final-pass lens gets one reviewer-approval artifact. Finding counts,
deferred work references, run history, and approval digests are derived from
the supplied review history rather than accepted as summary claims.

Missing reviewers, missing evidence, incomplete typed findings, failed lenses,
open actionable findings, unsafe deferrals, or a final-pass tree mismatch fail
closed. The provider writes only inside the consumer-supplied run root. The
consumer remains responsible for validating and publishing the returned
manifest through its existing evidence recorder.

Cancellation is accepted only for an active deferred attempt. Loss of the host
workflow before a terminal result becomes `indeterminate`; neither outcome can
become success later. Repeating an identical request is idempotent within the
provider session, while conflicting request or idempotency identities fail
closed.
