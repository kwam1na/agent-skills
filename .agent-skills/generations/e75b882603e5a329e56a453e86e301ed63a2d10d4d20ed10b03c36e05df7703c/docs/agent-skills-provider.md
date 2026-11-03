# Agent-skills delivery provider

The agent-skills provider is a small adapter over the published
`delivery-provider-rails/1` boundary. It does not select or run agents, own a
tracker client, or replace repository delivery policy. It accepts host-owned
workflow results, verifies that the requested workflow belongs to the current
installed release, reduces review results through `review-work`, and emits
ordered progress, evidence, blocker, and terminal documents.

Run the provider from the exact active generation directory as a line-delimited
JSON process:

```sh
python -B -m agent_skills.provider --root /path/to/adopting-repository
```

The consumer first sends `negotiate`, then a rail `request`. The opaque request
payload retains the consumer-owned gate, candidate, provider, attempt, and run
root fields. Its `agentSkills` object names:

- the exact active release identity and installed workflow id;
- host workflow results and their prepared tree identities;
- typed findings and the informational review timestamp;
- optional host events already produced by native tools.

The release identity must match all four fields in `.agent-skills/active.json`.
At startup, the provider takes a shared OS lifecycle lock without writing a
mutation-owner record, verifies its own module and portable workflow against
that generation's receipt, and keeps that snapshot stable until the process
exits. The operating system releases the shared lock even after forced process
termination. A source checkout, another installed generation, a release switch,
or a content mismatch fails closed before review evidence can be emitted.

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

Host references become session-random opaque values backed only by an in-memory
mapping. They cannot be recovered by hashing a small reference dictionary, and
they are never copied into deterministic manifests. Raw host payloads, actions,
credentials, idempotency keys, and transport details are never copied into
retained events or evidence. A host blocker or failure prevents success. The
provider neither retries mutations nor performs reconciliation itself.

## Review evidence

An aligned `review-work` result bound to the requested final candidate produces
one deterministic `delivery-evidence/1` manifest. With no payload capability
declaration the provider preserves `review.green/1`. When the consumer supplies
`acceptedPayloadSpecs`, the provider selects the first supported spec in
`review.green/2`, `review.green/1` preference order. Version 1 permits only
tracked actionable nonblocking P2/P3 `expansion` deferrals. Version 2 also
permits that shape at `in_contract` scope; neither permits adjacent or P0/P1
deferrals. Each
selected final-pass lens gets one reviewer-approval artifact. Finding counts,
deferred work references, run history, and approval digests are derived from
the supplied review history rather than accepted as summary claims.

Missing reviewers, missing evidence, incomplete typed findings, failed lenses,
open actionable findings, unsafe deferrals, or a final-pass tree mismatch fail
closed. The provider writes only inside the consumer-supplied run root. The
consumer remains responsible for validating and publishing the returned
manifest through its existing evidence recorder.

### One grace verification

After every selected lens aligns at the declared bound, a required change that
alters the candidate permits exactly one further verification with the same
lenses. Keep all prior rounds and the original `maxRounds`; this is not permission
to raise the bound or repeat review of an unchanged candidate. Dissent or failed
acquisition at the bound cannot qualify. A non-aligned grace is terminal, and a
second extra round is refused.

The host's review document may include this optional, closed object alongside
`requiredLenses`, `rounds`, `maxRounds`, and `findings`:

```json
{
  "graceVerification": {
    "previousCandidateRef": "4444444444444444444444444444444444444444",
    "candidateRef": "5555555555555555555555555555555555555555",
    "requiredChange": "Reference to the required candidate-changing correction"
  }
}
```

The provider requires these unequal references to match the actual adjacent
`preparedTreeSha` bindings for the aligned bound round and the grace round. It
does not establish whether a change was necessary: the host must retain that
decision and its evidence before acquiring grace. With a bound of four, retain
five rounds; the emitted manifest still names `pass-5`, keeps all five tree
bindings, and reports `iterationCount: 5`. The declaration remains caller-owned
input; raw change evidence is not copied into the redacted rail manifest.

Direct Python callers pass
`ReviewRequest(lenses, all_rounds, 4, GraceVerification(previous_ref, new_ref, required_change))`.
The returned `ReviewResult.grace_verification` retains that declaration. This
pure reducer cannot resolve opaque references, so its caller must bind them to
the adjacent retained acquisition envelopes. `obtain_review` itself remains an
ordinary candidate-bound acquisition with no new envelope keys. Omission of the
declaration preserves ordinary bound handling. Neither API observes future
candidate edits; a further required edit after grace remains the executor's
documented terminal condition.

### Reopen a round after base movement

The enhanced review input retains every obtained pass as a closed round object
with `passId`, logical `round`, full `candidate`, complete `results`, and optional
`supersedesPassId`. It also supplies `baseMoveReopenings`, each naming the exact
adjacent previous and replay pass plus an executed `delivered-diff-comparison/1`.
The comparison binds both candidates, base refs, base tips and merge bases. Its
`previousDiff` and `diff` arrays contain sorted `{path, content, sha256}` entries;
the provider verifies every content digest and requires exact entry equality.
The comparison has its own retained `evidenceRef`. An equal summary digest,
unchanged candidate label, or unexecuted comparison does not qualify.

The reducer keeps the superseded pass in history while using the replay as the
active result for that logical round. The replay does not spend the original
declared bound. Actual base movement, unchanged deliverable identity and digest,
complete required lenses, exact supersession, and an outer candidate equal to
the final active binding are all required. The emitted `runHistory` retains all
obtained pass IDs and tree bindings; `iterationCount` reports actual passes, so
obtained history and bound accounting remain distinct. This rule remains
separate from the single constrained grace verification.

Cancellation is accepted only for an active deferred attempt. Loss of the host
workflow before a terminal result becomes `indeterminate`; neither outcome can
become success later. Repeating an identical request is idempotent within the
provider session, and cached success is replayed only while its manifest and
approval digests still match. A different request is rejected while an attempt
is active; conflicting request or idempotency identities fail closed.
