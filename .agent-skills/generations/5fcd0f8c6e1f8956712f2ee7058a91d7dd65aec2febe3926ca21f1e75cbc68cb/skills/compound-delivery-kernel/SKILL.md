---
name: compound-delivery-kernel
description: Use when agent work needs one portable delivery posture across planning, implementation, review, and skill updates.
---

# Compound Delivery Kernel

## Purpose

Apply one small delivery loop across repositories while leaving repository
authority in the repository. The loop is:

Plan -> Work -> Review -> Compound.

## Plan

- Confirm the outcome, constraints, acceptance criteria, and non-goals.
- Read repository instructions and discover the smallest honest sensors.
- Choose `test-first`, `characterization-first`, or `sensor-only` posture.
- Capture the named scenarios before implementation.

## Work

- Isolate non-trivial work and preserve unrelated changes.
- Prove the selected posture before changing behavior.
- Implement the smallest slice that satisfies the named scenario.
- Run the narrowest relevant sensor after each meaningful slice.
- Classify failures as repairable drift, semantic blockers, or unrelated
  environmental blockers.

## Review

- Review against acceptance criteria, repository authority, and sensor evidence.
- Declare the delivery-review round bound before its first round; every round
  of delivery review counts, except one grace round obtained at the bound and a round reopened
  because the base moved under an unchanged deliverable identity; the default is
  four rounds. Unbounded plan-review rounds count against no delivery-review
  bound.
- Later rounds verify closure and review the delta; only P0 and P1 findings
  block; the lens, not the executor, closes or defers a finding.
- Decide the compounding response before the final review-and-merge loop, so a
  note is part of the reviewed candidate.
- Use specialized review only when the change's risk warrants it.
- Treat concrete findings as work, then rerun the proving sensor.
- Do not expand scope for speculative hardening or unrelated cleanup.

## Compound

Choose the smallest durable response to a reusable learning: update existing
guidance, add a focused sensor, save a solution note, record a follow-up, or
explicitly record that no durable learning emerged. Do not create documentation for obvious one-off work.
Name the tracked follow-up item recorded for every deferral any lens filed as
part of the delivery's record. One tracked item may carry several deferrals,
one item per shippable outcome. Where no tracker is configured, those follow-up
items are the actionable tracking handoff in the executor's delivery result
rather than a blocker.

## Report the Run

Every delivery workflow that applies this kernel reports its run through the
run-event command the repository's root instruction file declares, when it
declares one. The capability is optional: where the repository declares none,
proceed silently, with no handoff and no blocker. Reading the root instruction
file for that declaration is capability discovery: it comes before anything is
emitted, and everything else that file governs is read after the run starts.
What is emitted is observability, not evidence. No gate, approval, or
finish-line decision reads it, and a refused or failed emission never changes a
command's outcome or the delivery's result.

Report with the kinds the runtime already accepts, in version 2 of its event
grammar. Start the run with `--version 2`, because without it the runtime
starts a version 1 run, and give every emission on a version 2 run its own
`--event-id`, the same one for a retry of the same emission.

### The run lifecycle rule

- A workflow the operator invoked starts the run with `run.started`, naming the
  host and the installed workflow release it runs under, and the work item
  where there is one. So does an epic lane's `$execute-work`, which binds its
  run to its piece with the delivery's `bind-run`.
- A workflow another workflow invoked in the same session joins the current run
  and starts none. `$deliver-work` starts its run at `route`, and the workflows
  it routes to join it.
- The run stays open across every approval or decision the request waits on,
  each marked by `wait.started` and `wait.resolved` with `waitingOn: human`
  where the operator is needed, so a request routed through `plan` and
  `execute` is one run.
- Only the workflow that started a run ends it, with `run.ended`, when its
  request finishes (`complete`), stops on a blocker (`blocked`), or is stopped
  by the operator with work left (`partial`). `run.ended` carries the result
  category and the delivery's self-reported cost, the same cost the handoff
  reports, and nothing is emitted to the run after it.
- A request abandoned without an end leaves its run open. A start that the
  runtime refuses because the worktree has an open current run is made again
  with `--force`, which records the open run as `displacedRunId`. A start the
  runtime still refuses is silent, and the workflow goes on without a run.

Within the run, every workflow emits:

- `decision.recorded` at every fork resolved by judgement rather than by a
  discovered rule, naming the fork and the choice. Each workflow names its own
  forks.
- `blocker.recorded` for each typed blocker raised, naming its token.

### Stages

At each workflow's entry and exit, emit `activity.observed` as its stage
marker: `activityId: stage-<name>`, `phase: <name>`, `state: running` at entry
and `completed`, `failed` or `interrupted` at exit, a new `attemptId` for each
entry, and `owner` the host. `<name>` is one of `route`, `plan`, `diagnose`,
`execute`, `review` and `compound`: `$deliver-work` marks `route`, `$plan-work`
`plan`, `$diagnose-work` `diagnose`, `$execute-work` `execute` and `review`
around each review round it obtains, and compounding `compound`. A workflow
that joins a run marks its own stage in it. A stage entered again keeps its
`activityId`, takes a new `attemptId`, and names the stage's previous attempt as
`supersedesAttemptId`; every later state of that attempt repeats it, because
the runtime refuses a new attempt of an existing activity that names no
predecessor. Where no candidate exists yet, stage markers and waits carry the
worktree's `HEAD^{tree}` as `candidateTreeSha`. Lens attempts, executor
verification and the runtime's own command phases are not stage markers and
never take a `stage-` activity id.

Mark every stop for an approval or a decision with `wait.started` and
`wait.resolved`, with `waitingOn: human` where the operator is needed, and the
waiting party otherwise.

### The live view

Where the repository declares a live view command, a workflow that starts a run
as the operator's invocation runs it with `--if-absent` in the background and
gives the operator the address. A workflow that joins a run, and an epic lane,
does neither.

## Handoff

Report the changed outcome, sensors run, review result, residual risk,
compounding decision, and any blocked validation with its exact cause.
