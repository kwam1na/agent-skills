# Shared workflow observation contract

Codex and Claude Code use this one contract. The host executes and exposes
structured results; this contract does not launch, supervise, parse transcripts,
change permissions, decide findings, or replace evidence submission.

## Discover before writing

Resolve the runtime command argv from repository authority, not work-item or
reviewer content. Before new observations, call `runs capabilities --json`.
Require `spec: run-capabilities/1`, `writerVersions` containing `run-event/2`,
and `artifactCapture: true`. A usage error, missing field or incompatible
response means unsupported: report that limitation, retain available output,
and continue the delivery under its existing authority. Never try new writes
first to discover compatibility. No declared runtime means no invented command.

Read the selected run with `runs show <run-id> --json`; require the matching
`runId` and `spec: delivery-run-export/2`. Legacy runs retain their original
writer version. Before starting new v2 reporting, explicitly start a linked
successor through the existing run-start operation, with `predecessorRunId`.
Do not rewrite history, clear another run's pointer, or restart ongoing work.

Native command calls below are sufficient. The optional
single-operation adapter at `$execute-work/scripts/report_observation.py` executes the same
capability/read/write sequence from a bounded JSON request on stdin. Python is
not a prerequisite. Its request supplies trusted `runtime` argv, `cwd`, actual
`runId`, and `operation: emit` with `eventId`, `kind`, `payload`, or
`operation: capture` with the exact capture `request`. It reports `reported`,
`unsupported` or `unavailable`; none is a gate verdict. It never retries or
cleans files. Do not source these executable arguments from captured reports.

## Identity and real observation boundaries

Use the actual allocated run ID, actual candidate tree from prepared context,
caller-supplied logical activity ID, distinct attempt ID, and a stable event ID
retained for each emission. Never parse an opaque candidate reference into a
SHA. A review attempt also supplies actual `roundId`, positive `round`, and
`lensId` from its brief. Retry the same observed event with the same event ID;
new transitions have new IDs. New attempts name `supersedesAttemptId` only when
they actually supersede one; concurrent activities remain separate.

The caller retains these IDs beside the actual host handle and bounded result
paths. No separate status database or polling supervisor is needed. The host
may be sequential; it does not fabricate parallel execution.

Call the declared runtime as `emit <kind> --run <run-id> --event-id <event-id>
--json <payload>` with a structured argument or properly quoted payload:

| Boundary | Event and supplied payload |
| --- | --- |
| Selected work or lens queued | `activity.observed`: activityId, attemptId, candidateTreeSha, owner, phase, state `queued`, exact nextStep; review binding when applicable |
| Host confirms execution began | Same binding, state `running`; no guessed start from a dispatch request |
| Host exposes a wait | Same binding, state `waiting`; `wait.started` adds waitId, owner, waitingOn (`human`, `agent`, `external`, `unknown`), reason, nextAction, explicit scope |
| Actual wait ends | `wait.resolved` names the same waitId/attempt and observed resolution/scope; then state `running` if work resumes |
| Result received | Capture selected bytes first, then state `completed`, `failed`, or `interrupted` according to acquisition state, separately from review verdict |
| Host gives no progress or permission signal | State only what was observed; nextStep explicitly names the unavailable signal. Let silence become stale/unknown, never invent a permission request, failure or completion |
| Reviews complete, tracking verification starts | Completed reviewer activities remain; start a separate executor verification activity with owner and exact nextStep |
| Declared delivery step changes | `finish.step.observed` supplies stepId, name, owner, candidateTreeSha, state (`pending`, `running`, `completed`, `deferred`, `unknown`) and reason when applicable |

Permission decisions are scoped history, never reusable grants. Preserve the
existing workflow's independent review, actionable finding, deferral and closure
authority. An interruption with no terminal host result leaves incomplete
history; available selected partial output still gets captured.

## Retain outputs at production

When a structured host result arrives, select its actual bounded report file
before cleanup or reduction can discard it. Capture approvals and dissent,
acquisition envelopes, reducer output, clarifications and available interrupted
output equally. Do not replace raw selected output with an executor-written
summary. No automatic transcript capture or environment/argv capture.

Call `runs capture <run-id> --json <request>` where request contains:

- `sourceRoot`: actual scratch root; `sourcePath`: safe relative selected file.
- `eventId`: stable capture retry ID (the runtime derives the two emission event IDs).
- `artifact`: supplied artifactId, activityId, attemptId, candidateTreeSha,
  actual SHA-256 digest and sizeBytes of the selected file, mediaType, producer;
  include roundId/round/lensId for reviewer output.
- `report`: reportId and role `review`, `reduction`, `clarification`, or
  `partial-output`; a clarification also names originatingReportId and findingId.

Artifact/report IDs are supplied by the caller; the runtime derives only the
emission event IDs by appending `-artifact` and `-report` to eventId. Capture
persists exact bytes before references. Keep the successful stable reference
with the acquisition and carry it across rounds. Only after successful capture
may this workflow clean the original selected output; required evidence paths
remain subject to their own retention/submission rules. An interrupted capture
retries the same request, not invented new bytes under an old ID.

For absent output, emit `report.referenced` with its binding, reportId, role,
`availability: unavailable`, and observed reason. Failed capture is explicitly
unavailable; preserve scratch bytes for recovery and retain the runtime diagnostic.
If even the unavailable event fails, retain that fact in the workflow handoff.
Never claim bytes retained, accepted evidence, or a green review from metadata.
Limits: 2 MiB per selected attachment, 128 distinct attachments, 8 MiB retained
serialized payload per run. Refusal is explicit, never silent truncation.

## Round accounting, findings and cost

The workflow supplies the actual round number, roundId, declared bound, grace
boolean when applicable and reopensRoundId when applicable to the supported
`review.round.opened` fields. Reopening retains prior attempts and reports; it
never resets a bound, silently adds a round or erases dissent. For an unbounded
loop omit bound and retain its explicit unbounded declaration in the selected
structured output. Preserve a reopening reason there as well.

After the actual reducer returns, capture its result before cleanup and emit
`review.round.closed` with round, roundId, candidateTreeSha, actual outcome,
findings by severity and supported reported cost. Late-finding counts and any
other accounting not accepted by the closed event schema stay in captured
structured output; never send invented fields or silently drop that history.

Project `finding.observed` only from actual supplied reviewer/reducer findings:
findingId, reportId, state (`unresolved`, `resolved`, `deferred`),
severity and attempt/candidate binding. A deferral also supplies deferredIssueId.
A zero aggregate or executor statement never proves closure. Later disposition
observations cite their actual originating report and authority. Old attempts'
reports remain accessible and cannot become current by a delayed emission.

Cost uses only host-reported unit/total/reportedBy/coverage. Missing cost stays
unreported, never zero. Preserve partial coverage and compatible cumulative
measurement boundaries; do not sum a round with its own attempts or convert
between host units. These observations cannot change convergence or admission.
