---
name: obtain-review
description: Acquire an exact caller-selected review round and project its evidence into review-work without deciding convergence.
---

# Obtain Review

Use this workflow when the caller has selected review lenses and needs their
results collected for one exact candidate round. Do not choose additional
lenses, repair findings, change the candidate, or decide that delivery is done.

## Before realization

Require a nonempty ordered list of unique lens identifiers. Trim surrounding
whitespace, reject normalized duplicates, and accept only identifiers beginning
with an ASCII letter or digit followed by letters, digits, dots, underscores, or
hyphens. Do this before asking the host to realize any lens.

Obtain the trusted comparison context from the harness, separately from reviewer
output: exact release (`releaseId`, `profile`, `archiveSha256`,
`metadataSha256`), exact graph digest, versioned opaque subject and candidate
references, and a positive integer round. Never let an incoming result supply
its own trusted comparison values. Preserve opaque references exactly. The
caller also supplies the delivery-review round bound it declared, the same bound
`$review-work` reduces against. A plan-review loop declares no bound, so none is
supplied or invented for it. Bound accounting is not part of the harness's
comparison context.

Emit `lens.selected` before asking the host to realize any lens, naming the
mandated pair the caller declared, the full selected set, and the reason for the
selection, and emit `review.round.opened` for each round before its realization,
naming the round, the declared bound that round counts against, the candidate it
binds, and the lenses it carries. Without the bound beside the round a reader
cannot tell round 1 of 4 from round 1 of 1, and the `review.loop-bound-reached`
blocker is legible only against a bound the journal states. Where the round
belongs to a loop that declares no bound of its own, the emission names the
round and states that the loop is unbounded, which is what naming no bound
looks like where none may be invented. A grace verification round obtained at
the bound names that same declared bound and states that it is the grace round
obtained outside the count, because `$review-work` does not count it against
the bound, and a journal that named the bound alone would show a fifth round
counted against a bound of four. The mark describes the emission, not the
round: the grace round stays an ordinary verification round in kind, scope, and
carry-forward. A reopened round is emitted under the number it already carries,
with a member naming why it reopened. Emit both through the run-event command the repository's root
instruction file declares, when it declares one; where the repository declares
none, proceed silently, with no handoff and no blocker. Give the run's identifier to any lens realized in
another worktree of the same repository so its own emissions join this run; a
lens realized in a different repository shares no run and is given none. This
workflow keeps the caller-selected lens list unchanged; emission records the
selection and never decides it. The shared observation contract below maps this
accounting to the runtime's closed fields: unsupported explanatory members
(such as an unbounded declaration or reopening reason) stay in captured source
output, never in an invented event member.

Prepare one round brief per selected lens using the template below and the
harness-supplied realization or host convention. Native subagent dispatch and
the aggregation below are sufficient; Python and
`agent_skills.review_orchestration` are not prerequisites. Supply each selected
lens's bounded `contextRefs` and the expected result shape
`review-lens-result/1`: `outcome`, `findings`, `evidence`, and the optional
`lateFindings`. These refs identify
caller-retained material; they are not inline transcripts, arbitrary execution
payloads, or authority grants. Host realization
may be sequential. Launching, permissions, retries, and scheduling remain with
the active host and harness.

Every selected lens runs in its own clean checkout of the prepared candidate
tree, separate from the executor and every other lens. This applies whether the
realization is harness-supplied or follows the host convention, and whether the
lens intends to mutate. In that checkout, verify that the checkout's tree SHA
equals the prepared candidate tree and that its status is clean immediately
before the lens reads it. Repeat both checks after the lens finishes. A failed
check fails the acquisition; never accept a result from a checkout that moved
or stayed dirty. Temporary probes are permitted only in that lens's private
checkout and must be removed before the ending identity and clean-status checks;
never plant them in the executor's checkout or another shared checkout. The lens
names the reviewed tree SHA and both successful
identity checks in its evidence. This evidence uses the existing evidence
member; it adds no result or runtime-schema field.

## Observe each host realization

Apply the shared `$execute-work/references/run-observation-contract.md`.
The caller supplies the actual run, candidate tree, activity, attempt, round and
lens IDs in the brief. Emit queued only when selected and running only when the
host reports execution. Observe waits only from exposed native signals; do not
parse transcripts or change launch permissions. Both Codex and Claude Code use
the same fields, with missing signals explicitly unavailable.

Before cleaning a returned selected report file, call the runtime's `runs capture`
through that contract, preserving approvals, dissent, clarifications and available
interrupted bytes. Keep report/artifact references with the acquisition. Capture
failure remains unavailable and retains scratch output. Terminal acquisition
state is distinct from its review verdict. Preserve caller-supplied round
bound/grace/reopen accounting and all superseded attempts.

## Retain and project

Aggregate the host's acquisitions into a closed
`review-acquisition-envelope/1` containing the exact binding tuple,
`requestedLenses`, and `entries`. Retain one entry for every selected lens in
the caller's order, including missing, failed, and indeterminate acquisitions.
Copy the trusted binding tuple into the envelope and every entry; compare any
binding returned by a reviewer against that tuple and reject a mismatch.
Every entry has `lens`, `state`, and optional `independence` metadata. Only an
`obtained` entry carries `result`. A failure or unavailable native subagent is
recorded as an acquisition state, never replaced by an executor's own review.

- `obtained`: normalize an `aligned` or `changes-requested` result. Both need
  evidence; only `changes-requested` has nonempty findings. A result may also
  carry `lateFindings`, each naming one of that same result's own `findings`
  that the lens raised late; a lens carries none on its assessment round, and
  acquisition rejects the member outright in a first round, where no lens has a
  round behind it. Project each obtained result to `review-work` with its lens
  id, outcome, findings, evidence, and optional late findings.
- `failed`: project a failed lens with code `review-acquisition-failed`.
- `indeterminate`: project a failed lens with code
  `review-acquisition-indeterminate`.
- `missing`: retain its entry but omit it from reducer inputs, allowing
  `review-work` to report its existing missing-lens blocker.

Return both the copied complete envelope and the ordered reducer inputs. The
caller retains the envelope as evidence; this workflow does not persist it.
Unknown fields, malformed evidence, wrong versions, or any binding mismatch
reject. Acquisition has no round-level status or convergence output. A lens may
say `aligned`; only unchanged `review-work` reduces the complete round.

The Python helpers `prepare_review` and `obtain_review` are optional
implementations of this same contract when the caller has them. They validate
and project data; they do not launch lenses or establish host independence.
Their absence does not block native dispatch, aggregation, or the handoff to
`review-work`, and requires no helper installation or substitute runtime.

## Round kinds

Round kind is per lens. A lens's assessment round is its first result-carrying
round for the loop, and it reviews the whole review subject: the whole delivered
diff for delivery review or the whole plan for plan review. Every later
result-carrying round of that lens that advances the round counter is a
verification round, including a round obtained after alignment because the
candidate changed, and its delta runs from the candidate of that lens's most
recent result-carrying entry, so a round in which that lens's acquisition failed
is spanned rather than skipped. A grace round obtained at the bound is such a
verification round, like any other, with no special kind, scope, or
carry-forward. A reopened round keeps the kind and scope of the logical round it
replays.

The delivery's selected lens set is fixed before its first round and remains
fixed for every later round. For a verification round the caller supplies each
lens, as its `contextRefs`, one active retained entry from every prior round in
which that lens was selected, binding tuple and result together where the state
is `obtained` and binding tuple with its result-less state where the acquisition
failed. These entries are contiguous across the rounds in which that lens was
selected; for a delivery that means every prior logical round because its set is
fixed. A round in which the lens was not selected requires no nonexistent entry.
A replay's active entry replaces the superseded entry for carry-forward while
the superseded entry remains retained in history, so every supplied report
arrives bound to its round and its `candidateRef`. The caller also supplies, for
each of the two `candidateRef` values the delta spans, that reference's declared
resolution as a pair of the exact
`candidateRef` text (its `opaque` member) and the revision it resolves to; and
nothing else, nothing narrower. The previous candidate's `candidateRef` is read
from the lens's most recent result-carrying entry, never supplied beside it. An
assessment round spans no delta and needs no resolution pair.

`contextRefs` are material, not scope. The lens re-derives its carried set from
those entries rather than from any list the caller hands it, and derives the
delta itself from that entry's `candidateRef` and the current round's. A
`candidateRef` is opaque and is never parsed, so the adopting repository's
instructions declare how a `candidateRef` resolves to a revision, and the lens
matches each pair's `candidateRef` text by equality, never by parsing, against
its most recent result-carrying entry and the current binding. A lens that finds
its
carry-forward defective returns `changes-requested` with a P0 finding naming the
defect, which blocks and which only that lens can close: when the supplied
entries are not exactly its own active entries for every prior round in which it
was selected, when a prior round in which the lens was selected is accounted for
by neither an obtained entry nor a result-less one, when a resolution is
missing, unpaired, or paired with a reference that is neither of the two the
delta spans, or when any narrower scope is supplied. A lens cannot return
`failed`; the envelope's `failed` state is the caller's
record of an acquisition failure, carries no result, and is never how a lens
speaks.

In a verification round the realization asks each lens to re-check every carried
finding of its own, deferrals included, and report each one closed, open, raised
with its new severity, withdrawn with its reason, or still deferred, and only
then to review the delta between the previous round's candidate and this one. A
new finding outside the delta is filed only at P0; a P1 first filed outside the
delta is recorded as a deferral with a follow-up item.

Only P0 and P1 findings inside the round's scope are actionable. Deferrable
findings, P2 and P3 under the charter's severity vocabulary and a P1 first filed
outside the delta in a verification round, belong in `evidence` as deferrals
with a follow-up item rather than in `findings`. A carried finding's filed
severity and in-scope status are a floor: the filing lens may raise them on
re-check and never lower them, and a raised finding is treated as filed at the
new severity from that round, so a carried deferral raised to P0 blocks under
the out-of-delta rule. A carried P0 or P1 is discharged only by the filing lens's
closure report or by that lens's withdrawal with its reason, never by a later
deferral; a finding the lens filed as a deferral is discharged by that deferral.
A finding is closed, deferred, or declined only by the lens that filed it, in
its own report, with its reason. `aligned` means every carried finding
discharged and no actionable finding open.

A deferral's follow-up is a tracked item, and the executor records it, not the
lens. In its own report the lens records in its evidence what that item must
say: the outcome the item must reach, the deferral's identifier, and the lens
that filed it. A lens is read-only and files nothing itself. One tracked item
may carry several deferrals, one item per shippable outcome. Where no tracker is
configured, those follow-up items are the actionable tracking handoff in the
executor's delivery result rather than a blocker.

## Reopen a round the base moved under

A round obtained only because the base moved, under an unchanged deliverable
identity, does not count against the bound. Within that case the round counter
advances only when the candidate's deliverable identity differs from the round
before; where it is unchanged the replay reopens the round bound to the previous
candidate, whether that round is still open or already closed, carrying its
number, its kind, its declared lens set, and its carried findings.
A round obtained for any other reason counts against the bound even where the
identity is unchanged, so a round obtained to dispute a finding, to carry an
authorized repair, or to retry an acquisition that failed under unchanged
deliverable identity spends the bound like any other. The bound measures dissent,
not the merge traffic a delivery happens to sit behind.

The candidate's deliverable identity is the delivered change itself, not the
commit that carries it: identity is unchanged only when the diff of the new
candidate against its base is byte-identical to the diff of the previous
candidate against its base, over the same set of paths, with no path added,
removed, or renamed. For this comparison, a diff consists of each path and its
added and removed line bytes; hunk position headers and unchanged context lines
do not participate. The executor computes that comparison and retains its output
as evidence; a comparison that was not run, or that reports any difference at
all, is a differing identity, so a rebase whose conflict resolution altered one
delivered byte spends a round like any other change.
Nothing else may stand in for the comparison — not the reason the base moved,
not the disjointness of the upstream commits, and not a lens's agreement that
nothing changed.

A reopened round is realized like the round it reopens, keeping that round's
kind, its lens set, and its carry-forward rules, so the lenses see the candidate
the delivery will actually submit. When a round reopens, the lens's retained entry for that
round number is the replay's entry: the superseded entry stays in the delivery's
history but is supplied neither as carry-forward nor as that round's entry to
the reduction, so the entries a later round carries include no more than one
active entry per lens per logical round, and that later round's delta runs from
the replayed candidate.

The portable Python reducer receives every obtained pass in `round_history`.
Each `ReviewRoundPass` retains its unique `pass_id`, logical `round_number`, full
`ReviewCandidateBinding`, complete lens results, and, for a replay, the adjacent
`supersedes_pass_id`. The active `rounds` input contains the last pass for each
logical round; it never replaces or relabels the retained history. A
`BaseMoveReopening` names the exact adjacent previous and replay pass IDs and
carries a `DeliveredDiffComparison`. That comparison binds both candidate trees,
base refs, base tips and merge bases, and retains the executed comparison output
as sorted `previous_diff` and `diff` entries. Each entry contains the path, the
actual delivered-diff content for that path, and the SHA-256 of that content,
plus the comparison's evidence reference. The reducer verifies every content
digest and requires the two entry lists to be byte-identical. Equal caller
summary digests, an unchanged deliverable token alone, or a missing comparison
cannot reopen a round.

The provider exposes the same closed history with `passId`, `round`, `candidate`,
`results`, optional `supersedesPassId`, and top-level `baseMoveReopenings`.
Comparison members use the corresponding camel-case names and each diff entry
is exactly `{path, content, sha256}`. The provider rejects mixed legacy and
history shapes, unknown members, incomplete history, stale comparison bindings,
and an outer candidate that is not the exact final active candidate. Its
manifest keeps every obtained pass in `runHistory`; the reopened pass does not
spend the original declared bound.

## The round brief

The brief is [the round brief template](references/round-brief-template.md)
filled in, not composed. An executor realizing a lens fills the template's
members rather than composing the brief's shape from this text, and its members
are `Binding tuple`, `Round and bound`, `Round kind and scope`, `Fewest-rounds
mandate`, `Carry-forward entries`, `Candidate reference resolutions`, `Charter`,
`Result shape`, and `Observation binding and selected output`. This
section states what those members must say; the template states where each one
goes. The template is the shape of every lens brief, whether the realization is
harness-supplied or follows the host convention. Its `Charter` member contains
the selected charter verbatim inside the brief.

Realize every lens with a brief that states where in the loop its round sits.
The brief uses exactly one of these round statements:

- `ordinary counted`: round k of N, counted against the declared bound;
- `grace`: round N+1 with declared bound N, outside the count;
- `reopened`: reopened round k, preserving that round's number and naming why it
  reopened; or
- `unbounded`: unbounded round k, with no bound supplied or invented.

Plan review is the `unbounded` case because `$plan-work` leaves it boundless: its
rounds are realized outside
the bound-and-reduction machinery `$review-work` describes, count against no
bound that workflow reduces against, and reach no `review.loop-bound-reached`.
The brief also states the round kind and its resulting scope: an assessment
round reviews the whole review subject, while a verification round first
re-checks carried findings and then reviews its derived delta. For delivery
review the whole subject is the delivered diff; for plan review it is the plan.
A reopened round keeps its original kind and scope; a grace round is a
verification round.

The brief carries the mandate: every reviewer, in plan review and delivery
review alike, optimizes for the lowest number of rounds, and a finding the lens
can raise now is raised now. The mandate asks the lens for a conscious effort to
find everything it can in this round.

The brief states the expectation that follows: every finding the lens can
raise against lines unchanged since its own most recent result-carrying round
was expected in that round. A finding the lens raises after its assessment
round against lines unchanged since its own most recent result-carrying round
is still a finding, judged on its merits like any other, and the lens marks it
late in its own report. The mark is dissent about the lens, not about the
candidate, and it never changes what the round reduces to. Such a finding falls
outside that round's delta and remains subject to the out-of-delta rule:
`lateFindings` names only findings the result filed,
and a late finding that rule keeps out of `findings` is marked late in the
deferral it records instead.

For bounded delivery review, Round N, the last counted round, is judged exactly
as any other, and the brief says so. A grace round is judged under the same
mandate. A lens neither aligns because no repair room remains nor blocks to
force an escalation; the grace round and the deferral rule handle a real finding
raised at the bound.

## Realize a lens by convention where the harness supplies none

Where no harness-supplied realization exists for a selected lens, realize it by
this convention rather than by improvising one per delivery. One subagent per
lens. The filled brief includes the charter verbatim, and the executor composes
nothing about the charter itself. A lens is read-only. No two lenses in a round
share context. The evidence names the model class the lens ran under.

Evidence obtained by this convention records its realization basis as
`host-convention`, naming this section, rather than leaving the basis of the
realization unrecorded. The acquisition entry's typed `independence` is
unchanged by the convention and stays `{"status": "unverified"}` until
harness-supplied attestation evidence upgrades it, because a declared procedure
is not a proof of distinct realization. This section declares that procedure
only: it builds no realization runtime, and this workflow launches no
subordinate runtime.

## Independence and repair

Default independence to `{"status": "unverified"}`. A different label, prompt,
or persona proves nothing about realization independence.

An attested entry names `attestationRef` and `realizationRef`. Accept it only
against separately harness-supplied `review-realization-attestation/1` evidence
binding the exact release, graph, subject, candidate, round, lens, normalized
result digest, and a distinct realization. The digest covers all normalized
result fields using sorted-key compact UTF-8 JSON. The attestation also carries
`distinctRealization: true`; repeated realization references reject. The harness
authenticates this evidence and verifies distinctness upstream. Do not treat
attestations embedded in an untrusted envelope as trusted evidence.

If an authorized repair changes candidate A to B, obtain a wholly new round
bound to B, including every selected lens. Retain A's evidence as history, never
reuse it to satisfy B. The harness owns new-round allocation and candidate
capture; acquisition cannot advance a checkpoint or authorize the repair.
