---
name: diagnose-work
description: Establish evidence-backed causes for unknown failures during intake, planning, or remediation without authorizing or performing a fix.
---

# Diagnose Work

## Evidence first

Use diagnosis for unknown-cause work before assuming a plan or implementation
authorization, or as a subflow when a later stage uncovers an unexplained failure.
The host supplies the symptom, expected behavior, relevant prior attempts,
bounded observations, and available evidence under its own access policy.

Trace the complete causal chain from the original trigger to the observed
symptom. Support each link with retained evidence; correlation or a successful
symptom workaround alone does not establish the cause. Mark assumptions as
verified with evidence or still assumed, and retain evidence limits.

For each uncertain link, state a prediction that would also hold in another path
or scenario, obtain its observed result through the host, and compare the two.
A contradicted prediction cannot confirm the chain, even if a proposed fix
appears to work. Obvious evidence-backed links need no ceremonial prediction.
Inspect ruled-out hypotheses and their evidence before repeating a probe; retain
them and their reasons alongside the current chain.

## Outcomes and retention

- `succeeded` with output kind `confirmed`: the complete chain is evidence-backed
  and every uncertain link has a satisfied, observed prediction.
- `indeterminate`: proof is incomplete or contradicted, or reproduction remains
  unavailable or inconclusive.
- `blocked`: a required operational condition is missing.
- `failed`: obtaining or evaluating the diagnostic evidence failed.

Every non-success omits output and returns one actionable `nextStep`. Retain the
observations, hypotheses, assumptions, limitations, and test recommendations even
when confirmation is unavailable. Malformed inputs reject; they are not findings.
Recommend regression tests or a remediation handoff, never claim a fix was made
or that diagnosis authorizes one.

## Portable reducer boundary

`agent_skills.diagnosis.diagnose_work(observations, binding, context,
evidence_ref=...)` returns `DiagnosisResult(stage_result, evidence)`.
`DiagnosisObservations` contains ordered `CausalLink` values, optional
`Prediction` values, `Hypothesis` and `Assumption` values, reproduction evidence,
missing conditions, limitations, and recommendations. These are in-memory Python
integration inputs, not an execution-request or checkpoint format. The reducer
normalizes evidence; it does not run probes or persist the returned material.

The host retains `evidence` under its supplied opaque `evidence_ref` and admits
the closed `workflow-stage-result/1` envelope against independent harness
context. Bind the exact verified release, graph digest, and versioned opaque
subject. Omit the candidate before one exists; otherwise bind the exact current
checkpoint candidate. Never nominate a new candidate or derive trusted admission
context from the result itself.

Return to the same harness-managed checkpoint and recorded invoking stage.
Intake diagnosis hands evidence to planning; a later subflow returns to its
caller. Do not create, reset, or advance checkpoints or durable state. The harness
owns realization, permissions, retention, and finish-line authority. A separately
authorized canonical `implement` stage, realized by `execute-work`, may consume
the retained diagnostic evidence later; diagnosis never grants that authority.
