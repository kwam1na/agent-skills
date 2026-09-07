---
name: execute-work
description: Execute a bounded plan incrementally, preserve unrelated work, run repository sensors, and hand off evidence without overstating completion.
---

# Execute Work

## Purpose

Carry an approved plan through implementation and verification. Apply
`$compound-delivery-kernel` for the shared delivery posture. Preserve the plan's
scope, finish line, dependencies, test scenarios, sensors, approvals, and
execution posture.

## Start from Repository Authority

Read the adopting repository's instructions and current working state before
editing. Preserve unrelated tracked and untracked changes. Use the repository's
named commands and sensors; generic examples never replace a repository rule.

Do not begin implementation while a required dependency is incomplete. If the
request is diagnosis-only, stop after evidence-backed diagnosis. If the request
is plan-only, do not edit code.

## Work Incrementally

- For `characterization-first`, capture existing behavior before changing it.
- For `test-first`, prove the intended test fails before implementing the
  smallest behavior slice that makes it pass.
- For `sensor-only`, change only the declared non-behavioral surface.
- After each coherent slice, run the closest relevant sensor and preserve its
  result as evidence.
- Keep partial progress explicit. Do not broaden scope to make an adjacent idea
  fit the current delivery.

Before choosing a change surface from a failing sensor, confirm that its failing
assertion represents the repository's intended behavior rather than incidental
sensor mechanics. Read the assertion's stated contract and, when the distinction
is unclear, the smallest relevant history or neighboring evidence. If the
product already satisfies the intended behavior, correct the smallest sensor
defect without weakening that behavior. Do not reshape valid product behavior
solely to satisfy an accidental formatting or extraction constraint.

When work becomes blocked, retain completed work and evidence, name the blocker,
and provide the approval or decision handoff needed to continue.

## Use the Installed Product's Gate

When the repository declares the delivery product, invoke its installed command
through the repository's named entry point. The product owns preparation,
candidate identity, evidence validation, reuse, and accounting. Repository
commands remain sensors and policy inputs. Never reconstruct a receipt, invent
a passed check, or infer evidence reuse from journal events.

Run `prepare` before independent review so configured mechanical checks finish
before a review context is published. Retain the original
`review-context --json` document before dispatching reviewers. Give each lens
that exact context and its declared charter. After reducing actual independent
results, pass the concluded `review-outcome/1` document and its `contextDigest`
to `emit-review-evidence --context <retained-context>`, then use the returned
manifest with `submit-evidence`. Preserve every round, finding disposition,
acquisition failure, and coverage gap. A missing or failed reviewer cannot be
converted into approval by the executor.

Continue through `gate`, `record`, and `verify` with the repository's selected
validation and artifact obligations. The product's freshness result determines
whether another review or check is required; do not run an unconditional second
review on already-current evidence. Never waive malformed or mismatched
evidence. A human exception remains visibly waived, with its author, reason,
scope and candidate; an agent cannot grant one. Retain the final portable record
for the same verifier used in CI. Follow the repository's permitted finish line
after admission; the default is merge-ready, and merge or deploy requires the
authority the user or repository already supplied.

## Save Continuation Context

When the installed product provides recovery, use `save-context --json` at
meaningful stage boundaries with the bounded `contract` (`objective`,
`acceptanceCriteria`, `finishLine`) and current `stage`. Keep this in the
existing run; do not create a parallel session ledger. On continuation, use
`resume` and reconcile its unresolved actions before continuing dependent work.

Record `action.intent` before an authorized external action, naming its
`actionId`, `operation`, and reconciliation `reference`. After observing the
external state, record `action.observed` for the same ID and reference with
`succeeded`, `failed`, `not-performed`, or `unknown`. An intent does not authorize
the action, and an interruption does not establish its outcome. The native host
owns tools and authentication; resume never automatically repeats the action.

## Bound the Review Loop

Before the first review round, state the bound in effect for the delivery,
quoting the repository instruction that sets it if any, and state the lenses.
The mandated lenses are the two the delivery product ships, correctness and
testing, under the names the adopting repository declares for them. Every other
lens is the executor's own selection, chosen by the depth of the delivery, and
the declaration names the mandated pair, the full selected set, and the reason
for the selection. A repository cannot widen what counts as mandated.
Never declare a second bound for the same delivery. The review is bound to the
release installed when the delivery's first round is acquired: record a release
installed mid-review, keep every round already carried valid, and name the
bound release's `releaseId` and `archiveSha256` in the delivery's closing
record. The lens set is declared before the first round and held unchanged for
the delivery: every declared lens is selected in every round, no lens is added
after the first round, and dropping a lens disposes of nothing it filed.

For each verification round, supply each lens its own retained entry from every
prior round in which it was selected, result-less entries included, and, for
each of the two references its delta spans, a pair of the exact `candidateRef`
text (its `opaque` member) and the revision the repository instructions declare
it resolves to, and nothing else.

After each round, record a disposition for every finding, each citing the lens:
closed only by citing the closure report of the lens that filed it in a named
later round; deferred only where that lens itself recorded the deferral, with
its follow-up; declined only where that lens withdrew the finding, with its
stated reason; or open, named in the blocker when the bound is reached. The
executor closes, defers, and declines nothing on its own judgement.

Reaching the bound with dissent open, or with a failed acquisition and no round
left, is terminal. With dissent open, record the typed blocker
`review.loop-bound-reached` naming the open findings, report `partial`, and
stop. With the latest round blocked by a failed acquisition and no round left,
record the same blocker naming the failed lens, report `partial`, and stop.
Before the bound a failed acquisition is not terminal: obtain the next round
within the remaining bound, and that round is the latest the reducer judges.

When every lens is aligned at the bound and a required change would alter the
candidate, the delivery obtains exactly one grace verification round for that
change, at most once per delivery, declared as the grace round when it is
obtained. The grace round is an ordinary verification round under the same lens
set and the same carry-forward rules. If the grace round does not align, or a
further candidate change is required after it, record the typed blocker
`review.loop-bound-reached` naming what is open, report `partial`, and stop. The
grace round is not available with dissent open or where the latest round was
blocked by a failed acquisition.

Before the delivery reports done, every finding any lens recorded as a deferral
has a tracked follow-up item recorded through the tracker's `create-work`, one
item per shippable outcome, each deferral cited by its identifier and the lens
that filed it, related to the delivering work item. A deferral with no tracked
item is an open item for the finish line: record the typed blocker
`review.deferral-untracked` naming the deferrals that lack one, report
`partial`, and stop. Where no tracker is configured, those follow-up items are
the actionable tracking handoff in the result rather than a blocker.

## Name Blockers from a Typed Vocabulary

A typed blocker is a stable token, not prose. These are the tokens this workflow
raises:

| Typed blocker | Raised when |
| --- | --- |
| `review.loop-bound-reached` | The bound is reached with dissent open, the latest round is blocked by a failed acquisition and no round is left, or the grace round does not align or is followed by a further required candidate change. |
| `review.deferral-untracked` | The delivery would report done while a finding some lens recorded as a deferral has no tracked follow-up item, and a tracker is configured. |

Coin a new token only for a condition none of these names. A new token takes a
namespace the raising skill owns, is kebab-case in both its namespace and its
name, and is recorded on the work item the first time it is used, with the
condition that raises it.

## Keep the Delivery Loop Observable

Before claiming `complete`, record the outcome of every kernel phase rather
than collapsing the delivery into implementation plus tests:

- name the selected execution posture and the evidence that followed it;
- name the review round bound in effect for the delivery and the rounds obtained
  against it, and record a disposition for every finding, each citing the lens;
- name the release the review is bound to by its `releaseId` and
  `archiveSha256`, and any release installed mid-review; this list is the
  delivery's closing record for that identity;
- record the review outcome, who or what produced it, and the evidence reviewed;
  when no repository- or host-selected independent review is available, say so
  and retain the bounded acceptance-criteria review instead of implying
  independent review occurred; and
- record the compounding outcome and its reason or next action.

A missing posture, review, or compounding outcome leaves the delivery `partial`.
An unavailable review blocks completion only when repository authority, host
policy, or the approved plan requires that review.

## Observe current work and retained outputs

Apply the [shared observation contract](references/run-observation-contract.md)
before v2 emission. It is the same contract for Codex and Claude Code; discover
runtime capabilities and the selected run version before writing. Keep legacy
runs intact and report unsupported reporting explicitly without blocking delivery.
At actual work boundaries emit `activity.observed`, `wait.started`,
`wait.resolved`, and `finish.step.observed` with supplied identities. Keep
executor tracking verification visible after reviewers complete. Permission and
progress signals unavailable from the host remain explicitly unavailable.
Capture the caller's actual reducer/acquisition files before scratch cleanup,
including dissent and interrupted output; preserve failed captures for recovery.
Do not replace automatic product command observations with duplicate activities.

## Emit the Run's Events

Emit each event below through the run-event command the repository's root
instruction file declares, when it declares one, at the moment its rule names.
The capability is optional: where the repository declares none, proceed
silently, with no handoff and no blocker. What is emitted is observability, not
evidence. No gate, approval, or finish-line decision reads it, and a refused or
failed emission never changes a command's outcome or the delivery's result.

- `run.started` before reading repository authority, naming the host and the
  installed workflow release the delivery runs under. Reading the root
  instruction file for the run-event declaration is capability discovery, not
  the authority reading this point orders: it precedes `run.started`, because
  nothing can be emitted until the delivery knows whether a command exists to
  emit through. Everything else that file governs is read after.
- `ticket.read` and `posture.declared` once that authority and the work item
  have been read and the posture is chosen.
- `decision.recorded` at every fork resolved by judgement rather than by a
  discovered rule, naming the fork and the choice. Named forks: the branch
  name, the reason a review round was obtained on an unchanged candidate, a
  separate worktree for a lens that mutates, a skill read rather than invoked,
  a sensor considered and deliberately not run, and any value supplied by hand
  that should have been discoverable.
- `gate.reported` after a repository gate that is not a command of the
  installed delivery product, naming that gate's label, its outcome, and how
  long it ran.
- `pr.opened` when the change proposal is opened, bound to the candidate it
  carries.
- `blocker.recorded` for each typed blocker raised, naming its token.
- `run.ended` last, carrying the delivery's result category and its
  self-reported cost, the same cost the handoff reports.

This list is not the whole journal. `$obtain-review` emits `lens.selected` and
`review.round.opened`, `$compound-learning` emits `compounding.recorded`, and
this workflow, as `$review-work`'s caller, emits `review.round.closed` once that
reducer has closed a round. The two `$obtain-review` and `$compound-learning`
entries are owned by the skill named beside them, and this workflow neither
restates their rules nor emits them in that skill's place.

Every cost this delivery reports names its unit and that unit's coverage. Spell
the unit in kebab-case and identically for every run of the same repository, so
two runs compare rather than silently failing to; where the host meters the
tokens of dispatched subagents, that unit is `subagent-tokens`. Where the host
exposes no accounting for the executor's own session, report the part of the
cost the executor can see and say that it is a part, so the total is never read
as the whole.

## Verify the Finish Line

Run the smallest honest targeted sensors before broader repository gates. An
unavailable optional sensor is reported; an unavailable or failed required
sensor blocks completion. Required approvals must also be recorded before the
work can be complete.

Time a repository gate while it runs and retain that wall time beside its
outcome, because `gate.reported` needs how long the gate ran and a finished
run's duration cannot be recovered afterward except by running the gate again.
A sensor considered and deliberately not run is a judgement call rather than an
absence: record it with `decision.recorded` naming the sensor and why it does
not apply, so a skipped sensor is visible instead of indistinguishable from one
nobody thought of.

Use these result categories:

- `in-progress` when execution has begun without a completed slice;
- `partial` when useful work or evidence exists but the finish line remains
  incomplete;
- `blocked` when a dependency, required sensor, approval, or explicit blocker
  prevents progress or a completion claim; and
- `complete` only when the full scope and finish line are satisfied, required
  sensors pass, and approval handoffs are complete.

## Resolve Optional Tracking

When tracking is selected, use only neutral status, evidence, and
close-or-handoff operations with stable idempotency keys. Consume only
normalized outcomes. If no tracker is configured, continue the core execution,
perform no tracking mutation, and include the actionable tracking handoff in the
result.

## Handoff

Report the result category, execution posture, completed scope and finish-line
items, sensor results, retained evidence, review provenance and outcome,
compounding outcome, the delivery's self-reported cost, unresolved blockers,
pending approvals, and any optional operation that was not performed. Never
claim completion from progress alone or from a passing sensor set that does not
cover the declared finish line.
