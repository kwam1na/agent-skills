# Round brief: {{lens}}, {{key}} round {{round}}

## Binding tuple

- release: `releaseId` `{{releaseId}}`, `profile` `{{profile}}`, `archiveSha256`
  `{{archiveSha256}}`, `metadataSha256` `{{metadataSha256}}`
- graph digest: `{{graph}}` (SHA-256 of workflows/delivery-v1.json in the installed release)
- `subjectRef`: `{{key}}`
- `candidateRef`: `{{candidate}}`
- prepared candidate tree SHA: `{{tree}}`
- round: `{{round}}`
- lens: `{{lens}}`

Opaque references are reproduced exactly and are never parsed or abbreviated.

## Round and bound

{{bound_text}}

## Round kind and scope

{{kind}}

## Fewest-rounds mandate

Every reviewer, in plan review and delivery review alike, optimizes for the lowest number of rounds. A finding the
lens can raise now is raised now. Beside those rules, the mandate asks for a conscious effort to find everything the
lens can in this round.

Every finding the lens can raise against lines unchanged since its own most recent result-carrying round was expected
in that round. Such a finding raised later is still a finding, judged on its merits, and the lens marks it late in its
own report. The mark is dissent about the lens, not about the candidate.

For bounded delivery review, Round N, the last counted round, is judged exactly as any other. A grace round is judged
under the same mandate. The lens neither aligns because no repair room remains nor blocks to force an escalation.

## Carry-forward entries

{{carry}}

## Candidate reference resolutions

{{resolutions}}

## Charter

```
{{charter}}
```

## Result shape

Return `review-lens-result/1` as one JSON object, and nothing else, as your final message:

```json
{"schemaVersion": "review-lens-result/1", "lensId": "{{lens}}", "subjectRef": "{{key}}", "candidateRef": "{{candidate}}",
 "round": {{round}}, "outcome": "aligned | changes-requested",
 "findings": [{"id": "{{short}}-r{{round}}-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line",
   "failureScenario": "...", "proposedChange": "..."}],
 "evidence": ["..."]}
```

- `outcome`: `aligned` or `changes-requested`. Only `changes-requested` carries open P0 or P1 findings; both carry
  evidence.
- `findings`: every finding, with a unique id starting `{{short}}-r{{round}}-` and one severity, P0 to P3.
  - P0 and P1 are actionable and never deferred. In a verification round, a new finding outside the delta is filed
    only at P0; a P1 outside the delta is recorded in `evidence` as dissent instead.
  - P2 and P3 are deferrable: file each with `"state": "deferred"` and `"deferredIssue": "<what its follow-up item
    must say>"`. A deferred finding doesn't block `aligned`.
- `evidence` (strings, required): the reviewed tree SHA `{{tree}}` and the successful start and end identity checks;
  the re-check of every carried finding; the model class you ran under; the realization basis `host-convention`.
  End `evidence` with the approval conditions: for each finding you raise, whether its proposed change, adopted as
  written, resolves it.
{{later}}

A lens is read-only, files no tracked item itself, and cannot return `failed`.

## Observation binding and selected output

- Your private checkout: `{{checkout}}` (detached at `{{candidate}}`, prepared with the repository's own prepare
  commands, so its tests can run there). Run every command there, never in any other checkout.
- Before reading: verify `git rev-parse HEAD^{tree}` equals `{{tree}}` and `git status --porcelain` is empty. Repeat
  both after the review. If either fails, report that instead of a result.
- Temporary probes (e.g. mutants) are permitted only in this private checkout and must be reverted
  (`git checkout -- .` and removing any file you added) before the ending checks.
- Run ID: none (the repository declares no run-event command). activityId `review-{{key}}`, attemptId `1`, roundId
  `{{key}}-r{{round}}`, round `{{round}}`, lensId `{{lens}}`. Host: a native subagent; no progress or permission
  signals are exposed to you; record that limitation in evidence.
- Do NOT spawn subagents. Do not push, commit, or touch any checkout other than yours.

## In-app evidence so far

{{in_app}}

## Context refs (caller-retained material: ticket contract and plan unit)

{{context}}
