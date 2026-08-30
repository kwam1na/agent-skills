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
