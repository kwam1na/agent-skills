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
its own trusted comparison values. Preserve opaque references exactly.

Use `prepare_review` in `agent_skills.review_orchestration` to create ordered,
in-process host inputs. Supply each selected lens's bounded `contextRefs` and
the expected result shape `review-lens-result/1`: `outcome`, `findings`, and
`evidence`. These refs identify caller-retained material; they are not inline
transcripts, arbitrary execution payloads, or authority grants. Host realization
may be sequential. Launching, permissions, retries, and scheduling remain with
the active host and harness.

## Retain and project

Pass `obtain_review(envelope, lenses, context, attestations=...)` a closed
`review-acquisition-envelope/1` containing the exact binding tuple,
`requestedLenses`, and one ordered `entries` item per lens. Every entry repeats
the binding tuple and has `lens`, `state`, and optional `independence` metadata.
Only an `obtained` entry carries `result`.

- `obtained`: normalize an `aligned` or `changes-requested` result. Both need
  evidence; only `changes-requested` has nonempty findings. Project it to the
  existing `ReviewLensResult`.
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

## Round kinds

Round kind is per lens. A lens's assessment round is its first result-carrying
round for the delivery, and it reviews the whole delivered diff. Every later
result-carrying round of that lens is a verification round, including a round
obtained after alignment because the candidate changed, and its delta runs from
the candidate of that lens's most recent result-carrying entry, so a round in
which that lens's acquisition failed is spanned rather than skipped.

For a verification round the caller supplies each lens, as its `contextRefs`,
that lens's own retained entry from every prior round of the delivery in which
it was selected, contiguous over rounds 1 through N-1, binding tuple and result
together where the state is `obtained` and binding tuple with its result-less
state where the acquisition failed, so every prior report arrives bound to its
round and its `candidateRef`; plus, for each of the two `candidateRef` values
the delta spans, that reference's declared resolution as a pair of the exact
`candidateRef` text (its `opaque` member) and the revision it resolves to; and
nothing else, nothing narrower. The previous round's `candidateRef` is read from
the lens's most recent entry, never supplied beside it. An assessment round
spans no delta and needs no resolution pair.

`contextRefs` are material, not scope. The lens re-derives its carried set from
those entries rather than from any list the caller hands it, and derives the
delta itself from that entry's `candidateRef` and the current round's. A
`candidateRef` is opaque and is never parsed, so the adopting repository's
instructions declare how a `candidateRef` resolves to a revision, and the lens
matches each pair's `candidateRef` text by equality, never by parsing, against
its most recent entry and the current binding. A lens that finds its
carry-forward defective returns `changes-requested` with a P0 finding naming the
defect, which blocks and which only that lens can close: when the supplied
entries are not exactly its own entries for every prior round of the delivery,
when a round is accounted for by neither an obtained entry nor a result-less
one, when a resolution is missing, unpaired, or paired with a reference that is
neither of the two the delta spans, or when any narrower scope is supplied. A
lens cannot return `failed`; the envelope's `failed` state is the caller's
record of an acquisition failure, carries no result, and is never how a lens
speaks.

In a verification round the realization asks each lens to re-check every carried
finding of its own, deferrals included, and report each one closed, open, raised
with its new severity, withdrawn with its reason, or still deferred, and only
then to review the delta between the previous round's candidate and this one. A
new finding outside the delta is filed only at P0; a P1 first filed outside the
delta is recorded as a deferral with a follow-up.

Only P0 and P1 findings inside the round's scope are actionable. Deferrable
findings, P2 and P3 under the charter's severity vocabulary and a P1 first filed
outside the delta in a verification round, belong in `evidence` as deferrals
with a follow-up rather than in `findings`. A carried finding's filed severity
and in-scope status are a floor: the filing lens may raise them on re-check and
never lower them, and a raised finding is treated as filed at the new severity
from that round, so a carried deferral raised to P0 blocks under the
out-of-delta rule. A carried P0 or P1 is discharged only by the filing lens's
closure report or by that lens's withdrawal with its reason, never by a later
deferral; a finding the lens filed as a deferral is discharged by that deferral.
A finding is closed, deferred, or declined only by the lens that filed it, in
its own report, with its reason. `aligned` means every carried finding
discharged and no actionable finding open.

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
