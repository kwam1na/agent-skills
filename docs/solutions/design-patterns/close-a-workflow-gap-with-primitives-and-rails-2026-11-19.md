---
title: "Close a workflow gap with primitives and rails, not with rules"
date: 2026-11-19
category: design-patterns
module: run-delivery
problem_type: design_pattern
severity: high
tags: [run-delivery, orchestrate-work, merge-queue, lead, lane, workflow-gaps]
applies_when: "Shaping the fix for a gap a delivery hit, where the instinct is to tell lanes or leads to do something, or to make the tool refuse until they do"
---

# Close a workflow gap with primitives and rails, not with rules

## Problem

A delivery on the default branch kept losing time to gaps between its tools. A
story approved in review then failed the hand-off suite on two modules that read
files it had changed. The fix was a commit, so it cost a fresh round. Heavy runs
from a lane, two reviewers, the merge queue and another delivery stacked load
past twice the machine's cores. The merge queue's own sync merge sat on a lane's
branch and blocked the lane's push.

Each first draft of a fix was a rule. The first kind was written guidance:
"run the reader checks before your last round", "don't overlap the hand-off
suite with a review round", "batch your pushes". When the operator said
"the tool should handle the gaps", the second draft moved the same rules into
code: `review-prepare` refusing a round, the tool scheduling a lane's suite for
it. The operator rejected both: "make commands available and allow the agents to
make judgement calls. The tool shouldn't prescribe behavior because that doesn't
scale."

Both drafts failed for the same reason. A rule encodes one workflow's answer to
a question that every repository, machine and delivery shape answers
differently. "The last round" can't even be named, because lanes aim for as few
rounds as possible, so every round is meant to be the last.

## Solution

Work from first principles: name the facts the agents are missing. Then build
the smallest primitives that supply them, and lay rails on top.

1. **Name the missing facts.** Machine load, for example, came down to three:
   who is using the machine now, what a run will cost, and what the machine can
   bear. The late test failures came down to one: which modules read the files
   this diff changes, and which of them have passed at this head.
2. **Build primitives that supply them.** For load: a machine-wide claims
   ledger, a record of what each kind of run costs, and a capacity setting. For
   the reader checks: a query for "what reads these paths" and the evidence of
   which passed. Primitives are commands and data any agent, command or view can
   read.
3. **Lay rails: defaults with a recorded way off.** Commands do the common thing
   by default, such as claiming before a heavy run, waiting when the machine is
   full, or printing the reader checks before a round. An agent steps off with
   an explicit flag and a reason (`--now --why "..."`), and the reason goes in
   the journal. The views show those overrides, so a lead sees the judgement
   calls and their cost.
4. **Refuse only to protect shared state or another lane's work.** Never refuse
   to impose a workflow choice.

## Why This Matters

Rules don't scale and don't hold. A rule in a brief reaches only the agents who
read it at the right moment. A rule in code makes the tool wrong for every
delivery whose shape the rule didn't foresee, and agents route around it. A
missing fact, though, is the same gap everywhere. Once the tool supplies the
fact, each agent can make the right call for its own situation. Recorded
overrides then show over time whether the defaults are right, which neither
kind of rule can.

## Prevention

When a gap ticket's proposal or acceptance criteria say what an agent must do,
or what the tool refuses until it does, rewrite them:

- State the fact the agent lacked, and the command or data that supplies it.
- State the default the command takes, and the flag that steps off it with a
  recorded reason.
- Add a criterion that no fixed rule replaces the agent's judgement, and that
  any refusal protects shared state or another lane's work.

When a lead works around a gap by messaging a lane a new rule, treat the message
as a stopgap, and file the primitive alongside it.

## Examples

- **Machine load.** Not "one full suite at a time" or "don't overlap the suite
  with a round". Instead: a claims ledger, a cost record and a capacity setting.
  Heavy commands claim by default and wait when the machine is full, and
  `--now --why` runs anyway with its reason journaled.
- **Reader checks before review.** Not "run the readers before your last round",
  and not `review-prepare` refusing the round. Instead: `review-prepare` prints
  which modules read the changed files and which have passed at the head, and
  the lane decides.
- **The merge queue's sync merge on a lane's branch.** Not "the lead force-moves
  the branch back". Instead: the queue keeps its sync merges on a ref it owns,
  or a command restores a branch to its reviewed head under a lease, journaled,
  for the lane or lead to use.
- **The delivery's base.** Not the lead passing `--base main` on its own.
  Instead: setup offers exactly the bases the binding serves and records the
  operator's choice.

## Related

- `design-patterns/print-a-standing-rule-where-the-agent-acts-2026-11-11.md`:
  when an agent must keep doing something, put it in the command's output, and
  leave a trace that shows its absence. This note is the general case. Supply
  facts and defaults where the agent acts, and leave the call to the agent.
