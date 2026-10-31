---
title: "feat: Let the lead choose a candidate's checks, when the binding allows it"
type: feat
status: approved
date: 2026-10-30
execution_posture: test-first
context: An adopter's affected-checks provider widens a change to a few files into most of the repository's checks, about an hour a candidate, and pulls unrelated failures into the queue as base_broken. The owner asked for the lead's own focused choice to be a supported, recorded option of orchestrate-work rather than an adopter-only script.
---

# feat: Let the lead choose a candidate's checks, when the binding allows it

## Outcome

A binding can set `selection: {"lead": true}`. The lead then records, per candidate and reviewed head, the checks
that candidate runs, with a reason, and the runner checks it with those checks plus `alwaysRun` instead of the
provider's answer and `fallback`. Everything that shows checks says when they were the lead's, and why.

## In scope

- **Binding.** Optional `selection: {lead: boolean}`, off when absent, so every existing binding behaves as before.
- **Record.** A queue element's `leadSelection: {head, checks, reason, recordedAt}`, written only by the state tool's
  `select` command, which validates the checks against the binding and journals `check.selected`. Checks are full
  entries in the `alwaysRun` shape, so the runner's check machinery, ordering, prerequisites, profiles, timeouts and
  base re-runs take them unchanged; naming checks from the provider's last answer would need that answer stored.
  `select --clear` drops it. A runner-owned `checkSelection: {by, head?, reason?}` says whose checks the latest run
  used, written only under a binding that enables the option.
- **Runner.** Uses the selection while the binding allows it and its head is the row's reviewed head; no provider
  runs. A new hand-off head drops it; a selection whose head no longer matches is ignored with a note. A selection
  the binding no longer admits stops the candidate with the new `selection_invalid`.
- **Batches.** The union of members' selections, in member order, when every member has one; otherwise the provider
  selects for the whole batch, with a note naming the members without one. Two members defining one id differently
  stop the batch with `selection_invalid`.
- **Views and stats.** Result `selection.lead` lists each member's selection; the markdown view and static page say
  "checks selected by the lead ... why: ..." on the row, and a waiting or void selection; the line map's checking
  panel says the checks were chosen by the lead, with the reason; stats carry `checksSelectedBy`.
- **Docs.** The binding guide, lead checklist step 4, the stop table, close-out and the skill text, including the
  trade-off: the lead's judgment replaces impact analysis for that candidate, and the close-out `fullHealth` run is
  the backstop.

## Out of scope

- Any other host, compatibility engine or adopter overlay; the adopter's own provider.
- Choosing checks automatically from the diff: the choice is the lead's.

## Finish line

- The tests below pass, with the repository's suites, and a release set carries the change with runtime 0.7.7.

## Test scenarios

- Runner: a selected candidate runs exactly its checks and `alwaysRun`, with no provider; without a selection the
  provider selects; a stale head is ignored; a binding without the option ignores it and writes nothing new; a
  lead-selected check failing on the base too is `base_broken`; a selection the binding no longer admits stops with
  `selection_invalid`; batches take the union, fall back to the provider when a member has none, and refuse
  conflicting definitions.
- State tool: recording and journaling, the command line, refusals (disabled binding, wrong head, no reason, each
  invalid check shape), ownership (no patch writes `leadSelection`; only the runner writes `checkSelection`), a
  hand-off at a new head dropping it, and stats.
- Views: markdown and static page agree on the selection lines; the line map's checking panel names the lead's
  choice; the existing markdown pins stay byte-for-byte.
