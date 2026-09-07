# Round Brief Template

The brief an executor wraps around a charter when it realizes a lens by the
host convention. Fill every member below and supply the result to exactly one
lens. Composing a brief by hand from the workflow text is what this template
replaces, so a member that does not apply is filled with the reason it does not
rather than dropped.

## Binding tuple

The trusted comparison context, obtained from the harness and never from a
reviewer result:

- release: `releaseId` `<value>`, `profile` `<value>`, `archiveSha256`
  `<value>`, `metadataSha256` `<value>`
- graph digest: `<value>`
- `subjectRef`: `<exact opaque text>`
- `candidateRef`: `<exact opaque text>`
- round: `<positive integer>`
- lens: `<lens identifier>`

Opaque references are reproduced exactly and are never parsed or abbreviated.

## Round and bound

This is round k of N, where k is `<round>` and N is `<declared bound>`. State
one of:

- an ordinary round counted against that bound;
- the grace round obtained at the bound, outside the count; or
- a round reopened because the base moved, carried under the number it already
  holds, naming why it reopened.

Where the round belongs to a loop that declares no bound of its own, name the
round number and say the loop is unbounded instead of naming a bound.

## Fewest-rounds mandate

Every reviewer, in plan review and delivery review alike, optimizes for the
lowest number of rounds. A finding the lens can raise now is raised now. Beside
the round and the bound, this asks for a conscious effort to find everything the
lens can within the allocation, not across it.

Every finding the lens can raise against lines unchanged since its own most
recent result-carrying round was expected in that round. Such a finding raised
later is still a finding, judged on its merits, and the lens marks it late in
its own report. The mark is dissent about the lens, not about the candidate.

The last round is judged exactly as any other. The lens neither aligns because
no repair room remains nor blocks to force an escalation.

## Carry-forward entries

The lens's own retained entry from every prior round of the delivery in which it
was selected, contiguous over every round of the delivery before this one:
binding tuple and result together where the state is `obtained`, and binding
tuple with its result-less state where the acquisition failed. Nothing else,
nothing narrower.

- round 1: `<entry>`
- round 2: `<entry>`

An assessment round carries none. The lens re-derives its carried set from these
entries and derives the delta itself from the `candidateRef` of its most recent
result-carrying entry and this round's, so a round in which its acquisition
failed is spanned rather than skipped.

## Candidate reference resolutions

For each of the two `candidateRef` values the delta spans, a pair of the exact
`candidateRef` text (its `opaque` member) and the revision the adopting
repository's instructions declare it resolves to:

- `<exact opaque text>` resolves to `<revision>`
- `<exact opaque text>` resolves to `<revision>`

The lens matches each pair by exact text, never by parsing. An assessment round
spans no delta and needs no resolution pair.

## Charter

The selected lens's charter, supplied verbatim. The executor composes nothing
about the charter itself.

```
<charter text, verbatim>
```

## Result shape

Return `review-lens-result/1` with `outcome`, `findings`, `evidence`, and the
optional `lateFindings`.

- `outcome`: `aligned` or `changes-requested`. Only `changes-requested` carries
  nonempty findings; both carry evidence.
- `findings`: only P0 and P1 findings inside the round's scope are actionable.
  In a verification round a new finding outside the delta is filed only at P0,
  and a P1 first filed outside the delta is recorded as a deferral with a
  follow-up item rather than as a finding.
- `evidence`: the re-check of every carried finding of this lens, deferrals
  included, each reported closed, open, raised with its new severity, withdrawn
  with its reason, or still deferred; the deferrable findings, each with what
  its follow-up item must say, that item's outcome, the deferral's identifier,
  and this lens; the realization basis `host-convention`; and the model class
  the lens ran under.
- `lateFindings`: each names one of this same result's own `findings` that the
  lens raised late. A lens carries none on its assessment round. A late finding
  the out-of-delta rule keeps out of `findings` is marked late in the deferral
  that records it instead.

A lens is read-only, files no tracked item itself, and cannot return `failed`.

## Observation binding and selected output

Use the shared `$execute-work/references/run-observation-contract.md`.
Supply actual run ID, candidate tree from prepared context, activity ID, attempt
ID, roundId/round/lensId, stable event IDs, and selected bounded output paths.
Name a superseded attempt only for an actual replacement. Record the host handle
and any progress/permission signal limitation; do not parse transcripts.
Capture actual review, dissent, clarification and available partial-output bytes
before cleanup. Return stable artifact/report references or an explicit unavailable
reason. Permission observations never grant authority; reviewer verdict and
acquisition completion remain distinct.
