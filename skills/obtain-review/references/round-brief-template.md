# Round Brief Template

This template is the shape of every lens brief, for a harness-supplied
realization and for one realized by the host convention. Fill every member below
and supply the filled brief to exactly one lens. Composing a brief by hand from
the workflow text is what this template replaces, so a member that does not
apply is filled with the reason it does not rather than dropped.

## Binding tuple

The trusted comparison context, obtained from the harness and never from a
reviewer result:

- release: `releaseId` `<value>`, `profile` `<value>`, `archiveSha256`
  `<value>`, `metadataSha256` `<value>`
- graph digest: `<value>`
- `subjectRef`: `<exact opaque text>`
- `candidateRef`: `<exact opaque text>`
- prepared candidate tree SHA: `<tree SHA from the preparation receipt>`
- round: `<positive integer>`
- lens: `<lens identifier>`

Opaque references are reproduced exactly and are never parsed or abbreviated.

## Round and bound

Use exactly one of these statements:

- `ordinary counted`: round k of N, counted against the declared bound;
- `grace`: round N+1 with declared bound N, outside the count;
- `reopened`: reopened round k, preserving that round's number and naming why it
  reopened; or
- `unbounded`: unbounded round k, with no bound supplied or invented.

Fill k with `<round>` and N with `<declared bound>` wherever they apply.

## Round kind and scope

State exactly one kind and its scope:

- `assessment round`: this lens's first result-carrying round; review the whole
  subject and carry no prior entries or candidate resolution pairs. The whole
  subject is the delivered diff for delivery review or the plan for plan review.
- `verification round`: re-check every carried finding and then review the delta
  derived from this lens's most recent result-carrying entry and this round.

A reopened round keeps its original kind and scope. A grace round is a
verification round.

## Fewest-rounds mandate

Every reviewer, in plan review and delivery review alike, optimizes for the
lowest number of rounds. A finding the lens can raise now is raised now. Beside
those rules, the mandate asks for a conscious effort to find everything the lens
can in this round.

Every finding the lens can raise against lines unchanged since its own most
recent result-carrying round was expected in that round. Such a finding raised
later is still a finding, judged on its merits, and the lens marks it late in
its own report. The mark is dissent about the lens, not about the candidate.

For bounded delivery review, Round N, the last counted round, is judged exactly
as any other. A grace round is judged under the same mandate. The lens neither
aligns because no repair room remains nor blocks to force an escalation.

## Carry-forward entries

Supply one active retained entry from every prior round in which this lens was
selected: binding tuple and result together where the state is `obtained`, and
binding tuple with its result-less state where the acquisition failed. These
entries are contiguous across the rounds in which this lens was selected. A
delivery's fixed lens set makes that every prior logical round; another loop
requires no nonexistent entry for a round in which this lens was not selected.
A replay's active entry replaces its superseded entry here, while the superseded
entry remains retained in delivery history. Nothing else, nothing narrower.

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

The selected lens's charter, supplied verbatim inside this brief. The executor
composes nothing about the charter itself.

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
- `evidence`: the reviewed tree SHA and the successful start and end identity
  checks; the re-check of every carried finding of this lens, deferrals
  included, each reported closed, open, raised with its new severity, withdrawn
  with its reason, or still deferred; the deferrable findings, each with what
  its follow-up item must say, that item's outcome, the deferral's identifier,
  and this lens; and the model class the lens ran under. When this lens was
  realized by the host convention, also record the realization basis
  `host-convention`; otherwise record the harness-supplied basis.
- `lateFindings`: each names one of this same result's own `findings` that the
  lens raised late. A lens carries none on its assessment round. A late finding
  the out-of-delta rule keeps out of `findings` is marked late in the deferral
  that records it instead.

A lens is read-only, files no tracked item itself, and cannot return `failed`.

## Observation binding and selected output

Use the shared `$execute-work/references/run-observation-contract.md`.
Run this lens in its own clean checkout of the prepared candidate tree, separate
from the executor and every other lens. Before the lens reads the checkout,
verify that its tree SHA equals the prepared candidate tree SHA and its status is
clean. Temporary probes are permitted only in this private checkout, never in
the executor's checkout or another shared checkout, and must be removed before
the ending checks. Repeat both checks after the review. A failed check fails the
acquisition; do not return a lens result from a checkout that moved or stayed
dirty. Supply actual run ID, candidate tree from prepared context, activity ID, attempt ID,
roundId/round/lensId, stable event IDs, and selected bounded output paths.
Name a superseded attempt only for an actual replacement. Record the host handle
and any progress/permission signal limitation; do not parse transcripts.
Capture actual review, dissent, clarification and available partial-output bytes
before cleanup. Return stable artifact/report references or an explicit unavailable
reason. Permission observations never grant authority; reviewer verdict and
acquisition completion remain distinct.
