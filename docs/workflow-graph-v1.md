# Portable workflow graph and stage results

`workflows/delivery-v1.json` is the canonical `workflow-graph/1` artifact.
`schemas/workflow-graph.schema.json` freezes its entire semantic matrix with a
Draft 2020-12 `const`, rather than allowing a shape-valid graph to weaken a
prerequisite or redefine a stage. `schemas/workflow-stage-result.schema.json`
defines the closed `workflow-stage-result/1` envelope. These artifacts contain no
release digest of their own. Hash the exact graph bytes after building the
release; bind that digest and the verified archive identity at admission time.

## Ownership

The graph declares semantics, prerequisites, required and optional input kinds,
success output kinds, mutation classes, evidence-adapter slots, permitted
statuses, candidate binding, and conditional edges. It neither realizes stages
nor owns execution attempts. The active host realizes the canonical `implement`
stage using `execute-work`; `execute-work` is not another stage or an alias.

The harness owns run/checkpoint state, prerequisite admission, independently
captured subject/candidate identity, authorization, policy, host realization,
scheduling, retries, fencing, disclosure, and durable evidence. Graph validation
checks consistency with that independently supplied context; it does not prove
that the context is authentic. A stage result cannot nominate its trusted
comparison candidate, create an approval, advance a checkpoint, complete a
managed run, or grant permission for an external action.

## Public Python boundary

The production module is standard-library-only:

```python
from agent_skills.workflow_graph import (
    apply_overlay, graph_sha256, load_graph, validate_edge,
    validate_graph, validate_reference, validate_release, validate_stage_result,
    WorkflowGraphError,
)

validate_stage_result(result, admission_context, graph=load_graph(), overlay=[])
validate_edge(result, "plan", admission_context, overlay=[])
```

Validation returns `None`, or raises `WorkflowGraphError`. It does not mutate
the result or context. `apply_overlay` returns a copy retaining all ten canonical
declarations; only effective requiredness, disposition, and omission explanation
can change. `load_graph()` reads the released artifact. `graph_sha256()` hashes
its exact bytes, including its final newline, not a normalized reserialization.

The `admission_context` mapping is a Python integration input, not a new
serialized execution request or durable attempt document. It contains:

| Field | Meaning |
| --- | --- |
| `release` | Exact verified release projection: `releaseId`, `profile`, `archiveSha256`, `metadataSha256` |
| `graphSha256` | SHA-256 of the exact canonical graph bytes |
| `subjectRef` | Versioned opaque subject reference |
| `candidateRef` | Optional independently captured current checkpoint candidate |
| `producedCandidateRef` | Required independently captured new candidate for successful `implement` admission |
| `completedStages` | Harness-retained, already admitted prerequisite summaries indexed by canonical stage id |
| `diagnosisInvoked` | Boolean: the plan/implementation path invoked diagnosis and therefore requires confirmed evidence |
| `diagnosisFrom` | For `diagnose`, its recorded invoking stage (`intake`, `plan`, `implement`, `review.acquire`, `review.reduce`, or `feedback.handoff`) |
| `invokingStageRef` | Nonempty retained invocation evidence reference for a diagnostic subflow; not needed for pre-run intake diagnosis |
| `repairFrom` | When acquiring a repair round, `review.reduce` or `feedback.handoff`; absent for the initial round |

A successful prerequisite summary is
`{"status": "succeeded", "outputKind": "bounded-plan", "evidenceRef": "opaque"}`.
The harness must supply the current admitted evidence for the exact subject and
candidate; a result cannot supply or overwrite this map. Non-success summaries
may be retained, but cannot satisfy prerequisites. Back edges retain the same
checkpoint, subject, prior admitted prerequisites, and current candidate.
Required input kinds are graph declarations for the host's stage realization;
they are not executable requests or inline source/document payloads.

The subject shape is
`{"schemaVersion": "workflow-subject-ref/1", "opaque": "..."}`. The candidate
shape is
`{"schemaVersion": "workflow-candidate-ref/1", "opaque": "..."}`. Opaque values
must contain non-whitespace text. They are compared exactly and never parsed,
trimmed into a different identity, or assigned durable meaning. Digests are 64
lowercase hexadecimal characters. Release identity is exactly the public
four-field `VerifiedRelease` projection, not a loosely matched version label.

## Candidate binding

| Policy | Result requirement |
| --- | --- |
| `forbidden` | Omit `candidateRef`, even if the harness already has a candidate |
| `required` | Include the exact current checkpoint candidate; no candidate means rejection |
| `checkpoint-contextual` | Include the exact current checkpoint candidate when one exists; otherwise omit the field |
| `produced-on-success` | Success binds independently captured `producedCandidateRef`; non-success binds the prior checkpoint candidate or omits the field before an initial candidate exists |

Thus initial implementation success binds B, initial failure omits a candidate,
revision A-to-B success binds B, and revision failure retains A. A result cannot
name B and make B trusted by setting a field in its own envelope. Missing,
speculative, stale, or wrong-version candidate references reject.

## Closed results and typed handoffs

Every result requires `schemaVersion`, `release`, `graphSha256`, `stageId`,
`subjectRef`, `status`, `evidenceRefs`, and `limitations`. Only candidate-binding
rules permit `candidateRef`. Evidence and limitation lists contain unique,
nonempty strings. There is no inline credential, transcript, arbitrary payload,
permission, approval, checkpoint, attempt, cancellation, or scheduling field.
Unknown fields reject at every envelope/reference/output boundary.

Success uses `status: succeeded` and a closed output:
`{"kind": "confirmed", "evidenceRef": "retained-output"}`. The kind must be one
of that stage's declared success outputs. `blocked`, `failed`, and
`indeterminate` are statuses, never output kinds; they must omit `output` and
include exactly one nonempty actionable `nextStep` string. Each stage declares
which statuses are available. Non-success does not grant a successful graph edge.

`publish.handoff` and `feedback.handoff` success outputs additionally require
`adapterRef` matching the graph's named adapter slot. These names are neutral
harness/adopter binding references, not transports, operations, credentials, or
authority. The graph names `review-evidence`, `publication-evidence`,
`feedback-evidence`, and `finish-line-evidence` required slots; diagnostic evidence
has an optional slot. The host supplies and enforces concrete bindings before
realization. This module does not create them or test a remote service.

Feedback success also requires `trust: untrusted`, including empty normalized
feedback. Upstream normalization permits evaluation, never instruction trust or
repair authorization. `compound` is decision-only: `learning-required` requires
an opaque `preservationHandoffRef` for repository-owned capture;
`no-reusable-learning` does not. Neither output may claim knowledge was written.

## Overlays, prerequisites, and edges

An overlay is a list of unique closed stage overrides. Each has `stageId` and may
strengthen `requiredness` to `required`. It may set `disposition: omitted` only
for canonical `policy-skippable` stages that have not been strengthened. Omission
requires `policyRef` and `reason`, both nonempty. Required stages never disappear;
duplicate entries cannot strengthen and then omit a stage. An omitted stage
cannot report any result, including success. Included is the default, so a
policy-skippable but non-omitted handoff still needs successful evidence.

For example, omitting publication and feedback requires two retained declarations:

```json
[
  {"stageId": "publish.handoff", "disposition": "omitted", "policyRef": "policy-1", "reason": "Publication was not requested."},
  {"stageId": "feedback.handoff", "disposition": "omitted", "policyRef": "policy-1", "reason": "No publication was requested."}
]
```

Prerequisites in graph data specify `always`, `diagnosis-invoked`,
`diagnostic-subflow`, `repair-loop`, or `effective` conditions, allowed output
kinds, and `allowOmitted`. Only an explicit `allowOmitted: true` accepts omission.
Strengthening diagnosis to required activates its plan/implementation prerequisite
even when the caller has not reported a diagnostic invocation.
`finish.verify` requires aligned review plus successful or validly omitted
publication and feedback. Blocked, failed, indeterminate, absent, or wrong-kind
required handoff evidence cannot satisfy it.

`validate_edge` checks the graph's declared source/output predicate. Review
changes may take diagnosis/implementation repair edges; aligned review may only
proceed to publication, or to finish verification when publication is omitted.
Feedback may take repair edges only with `feedback-available`; `feedback-empty`
can only proceed to finish verification. Diagnosis after intake goes to `plan`;
a later diagnosis returns only to its recorded caller. Terminal `compound` has
no next edge. Checking an edge does not admit a new candidate or mutate a run.
The receiving stage must still be admitted using its independently captured
current context.

## Independent interoperability sensor

Install the exact test engine into the active interpreter:

```bash
python -m pip install -e ".[test]"
python tests/test_workflow_graph_contract.py
```

The optional extra pins `jsonschema==4.23.0`; production has no runtime
dependency. CI installs it for every supported operating system. Use that same
virtual-environment interpreter for isolated (`-I`) qualification subprocesses;
user-site packages are not visible there. Explicit setuptools discovery selects
only `agent_skills*` from the flat repository. Editable installation supports
development tests; deterministic repository archives remain the release surface.

`tests/consumers/workflow_consumer.py` imports no production module. It uses the
independent Draft 2020-12 engine on the public schemas plus its own checkpoint
and candidate context, and separately evaluates graph predicates. Tests submit
shared planted vectors to both peers without using one peer as the other's
schema oracle. They plant a weakened runtime decision and weakened schema to
prove divergence is visible, and exercise every stage's statuses/output kinds,
all declared edge predicates, same-checkpoint diagnosis, omission, candidate
replacement, unknown fields, and authority-negative handoffs.

`tests/fixtures/workflow-graph/result-templates.json` contains deliberately
unbound templates, not qualification evidence. Test orchestration binds exact
release, graph, subject, and candidate references at runtime. No fixture or graph
embeds the digest of its own containing archive, avoiding a release-hash cycle.
