# Portable autonomous delivery tranche — delivery evidence

## Authority and finish line

- Approved plan: `docs/plans/2026-08-29-portable-autonomous-delivery-tranche.md`
- Plan SHA-256: `d926c344ce1c545f4ebf376cbe6a47ad42a7ffe7d9f1c4873bff5bd390081ea0`
- Plan review: adjacent `.review.json`, unanimous across coherence, feasibility,
  product, security, scope, and adversarial lenses on that exact digest.
- Worktree branch: `codex/portable-autonomous-delivery-tranche`
- Starting `origin/main`: `f0a058d7b40a38bbe43c007f8b11248ecd4bda6a`
- Finish line: one scoped merged PR, passing required local/hosted checks and
  exact core, Linear, and provider qualification; no deployment.

## Tracking

Resolved from the existing portable-skills workstream: Yaegars (`V26`), Athena.
The completed prior epic is related, not reopened or duplicated.

| Unit | Linear | Posture | Dependencies |
| --- | --- | --- | --- |
| Batch | [V26-1445](https://linear.app/v26-labs/issue/V26-1445) | coordinated single integration PR | prior epic V26-1412 is complete |
| Provenance | [V26-1446](https://linear.app/v26-labs/issue/V26-1446) | characterization-first, then test-first | none |
| Graph/result contract | [V26-1447](https://linear.app/v26-labs/issue/V26-1447) | test-first | provenance |
| Diagnosis | [V26-1448](https://linear.app/v26-labs/issue/V26-1448) | test-first | provenance, graph/result |
| Review acquisition | [V26-1449](https://linear.app/v26-labs/issue/V26-1449) | test-first | provenance, graph/result |
| Release/qualification | [V26-1450](https://linear.app/v26-labs/issue/V26-1450) | characterization-first; sensor-only records | all implementation units |
| Final review/merge | [V26-1451](https://linear.app/v26-labs/issue/V26-1451) | sensor-only after freeze | release/qualification |

All parent links, project/team bindings, and blocking relations were read back
and verified after creation. No labels were added.

## Execution evidence

The implementation slices and exact qualifications below are complete. Final
whole-candidate review, hosted checks, and merge evidence are retained in the
integration PR and tracker so recording a verdict does not mutate its candidate.

### Provenance characterization and origin acceptance

- Baseline registry validation: zero findings; eight registry mutation tests pass.
- Baseline full characterization: 202 tests, with exactly three failures in the
  standing-document identifier sensor caused by this tranche's plan/review/
  delivery records. No runtime baseline failures were observed. Release integration
  will exclude explicit delivery-record directories while preserving the standing
  source prohibition, as required by `AGENTS.md`.
- Migration red: three new positive cases fail because the baseline rejects
  provenance version 2 and enforces one global source revision.
- Migration green: nine provenance tests, eight registry mutation sensors,
  fifteen validation tests, and eleven release-archive tests pass; registry
  validation has zero findings.
- Independent Draft 2020-12 engine check: schema valid; live version 2, retained
  version 1, and independent-origin examples pass; missing rationale rejects.
- Spec review independently confirmed the live migration, focused sensors, and
  retained archive. A separate Python-quality reviewer approved with no actionable
  findings. The unit is in review pending the coordinated integration PR.
- Independent graph review exposed JSON Schema terminal-newline digest acceptance;
  the same parity check was applied to provenance. Four failing schema cases
  preceded exact 40/64-character constraints. All eleven provenance tests then
  passed, and narrow independent Python review reconfirmed alignment.
- A core archive built before migration still verifies and extracts under the
  migrated runtime: release `provenance-baseline-v1`, archive SHA-256
  `0ec04c68ab0ca9a919e6e19c2caec45bac98ac754fd36e904b7ef7f88a600e3b`.

The delivery agent accepts the following origins under the user's explicit
delegation to plan and implement this tranche in task
`01a04fe6-13db-76b0-b049-b66771e85314`. This is delegated delivery approval, not a
claim that an external maintainer separately reviewed the hashes.

- `diagnose-work`: repository-derived from `kwamina/athena`, immutable revision
  `342850073464a2c9b6198d32d11475f5fa5cbe53`, path
  `.agents/skills/ce-debug/SKILL.md`, source SHA-256
  `6bc5dd51457bb4a640f15b12609912b53bda193699def51e3bfce5966dabd11a`.
  The source is byte-identical at the inspected current Athena revision; no
  global provenance baseline is advanced.
- `obtain-review`: independently authored portable acquisition semantics from
  the approved contract, not an Athena derivative. Entry owner and approval
  owner are `delivery-agent/user-delegated`; approval reference is
  `codex-thread:01a04fe6-13db-76b0-b049-b66771e85314`. Its final output digest is
  recorded only after the actual skill bytes exist.

### Graph and normalized stage-result slice

- Added the complete canonical ten-stage graph, closed stage-result schema,
  standard-library validator, and independent harness-shaped consumer using
  pinned `jsonschema==4.23.0`.
- Initial red failed on the absent graph module; initial green covered thirteen
  scenarios, including the intake/diagnosis/plan slice and same-checkpoint return.
- Independent spec review found terminal-newline digest schema drift and the
  missing nonempty contextual-candidate mismatch vector. Eight schema failures
  preceded exact digest-length constraints; diagnosis and plan now both test
  candidate B against checkpoint A. All fifteen focused tests pass.
- Spec re-review and separate Python-quality review align with no remaining
  findings. Quality review also checked 120 representative malformed vectors
  without peer divergence and a standard-library-only import. Registry/release
  integration remains a later unit.
- An isolated editable install imports the pinned engine under isolated Python;
  production runtime remains standard-library-only. CI installs the optional test
  extra on its existing operating-system matrix.

### Evidence-first diagnosis

- Missing-module red preceded the pure diagnosis reducer, concise portable skill,
  and canonical scenario fixture. All fifteen focused tests pass.
- Independent spec and Python-quality reviewers aligned. Additional probes
  checked actual reducer output through the independent consumer, malformed
  inputs, reference retention, and input/output alias isolation.
- The skill-creator structural validator passed. A fresh host forward test
  confirmed an in-memory reproduced empty-input division failure, but returned
  indeterminate for a cache hypothesis contradicted by bypass observations.
  Both retained the original checkpoint; neither performed or authorized a fix.
- Forward-test raw reports and replay scripts remain disposable local evidence,
  not a new packaged scenario oracle. The host supplied a concrete next trace
  request where the reducer's bounded next step was generic; one assumption was
  imprecisely labeled in the host report without changing the diagnosis outcome.

### Caller-selected review acquisition

- Missing-module and missing-independent-consumer-path red preceded the pure
  acquisition/projection implementation. Fourteen acquisition tests, ten existing
  review/compounding tests, and fifteen graph tests passed before quality review.
- Spec review aligned. Quality found non-UTF-8-encodable reviewer text could be
  accepted unverified or raise a raw encoding exception during attestation
  hashing. Eight failing subcases preceded stable rejection; all sixteen
  acquisition plus ten existing review tests pass, and quality re-review aligns.
- Skill structural validation and a fresh host forward test passed. The host
  retained a missing lens, rejected relabeled candidate-A entries for candidate B,
  accepted a wholly fresh B round, kept independence unverified, and handed
  convergence to the existing reducer without invoking it during acquisition.

### Exact release integration baseline

- Before provider qualification changes, the retained pre-migration archive
  passed the existing provider qualification: ten scenarios, forty-six assertions.
  This characterizes compatibility, not support for the new tranche.
- Registry/release closure, core qualification, and Linear binding integration
  used disjoint worker ownership. Provider qualification and final
  README/delivery evidence were owned by the coordinating agent.
- Five integration tests cover exact payload membership, deterministic builds,
  extracted standalone validation, installation/update/offline rollback, both
  profiles, and both fixed hosts. Independent spec and quality reviews aligned.
- The new graph and skills are part of the release closure; qualification records,
  frozen collector input, and delivery documents are excluded from archive hashes.
- Core qualification runs trusted behavioral sensors against extracted candidate
  imports and independent consumer bytes. Negative tests reject planted semantic
  drift, skipped/empty sensors, stale-success output, and candidate-input aliases.
- Independent review found malformed evidence and nonzero subprocess exits could
  be accepted as core success. Eleven failing subcases preceded strict evidence
  shape/count and exit-status checks; eight focused tests and independent
  re-review then passed. Honest failed records retain their original evidence.
- Linear qualification now requires an independently frozen collector input and
  version-2 bound attestation. Eighteen focused tests and independent spec and
  quality reviews passed before live collection. Provider qualification also
  checks actual installed graph and skill bytes and rejects vacuous evidence.

### Frozen candidate and fresh qualification

The final review round resolved three additional in-contract findings:

- Repair-loop prerequisite coverage: paired production/consumer vectors now
  cover both repair origins, missing/non-success/wrong output/evidence, and
  invalid origins. A bypass that removes `repairFrom` causes twenty-two test
  failures; the actual sixteen-test graph suite passes. The new sensor is part
  of core qualification.
- Provenance version-2 modification parity: thirteen failing cases preceded
  closed modification status/reason validation. All thirteen provenance tests
  pass; independent review checked eighteen malformed schema/runtime pairs and
  the retained actual version-1 archive still verifies.
- Shared-core qualification: a genuinely built divergent Linear archive was
  incorrectly accepted with a passing core record. Linear qualification now
  rebuilds its core projection through the existing release builder and compares
  exact archive/metadata identity. The regression rejects the mismatched archive
  without producing a record; independent review confirmed matching archives
  still pass. Nineteen focused Linear tests pass, excluding only the checked
  record replay before its refresh.

The first full integration suite found four provenance test-helper failures from
adding repository fields to independent origins. The helper now preserves
already-versioned origins; the subsequent 291-test suite passed before these
last three review fixes. The final full-suite result is retained in the PR.

Both profiles were rebuilt a second time immediately before live collection;
their archive and metadata identities matched the frozen input exactly.

| Profile | Archive SHA-256 | Metadata SHA-256 |
| --- | --- | --- |
| core | `25dd462a818cf2134c08be27181ba123adfa74bf2c367884a411b8b664523fc6` | `e3b4904148b45df90937f2f383f1ef1e5cb0ba60a27291602edf89609c3a3ffa` |
| linear | `7a6945ffb0ea1f119a49e4d91085e802395858c2032fce5a696e7d26ed3cec54` | `b277ad9ac31b42b527ec1bc223e5b38553a299d00e53a0512922302fdd38bf2b` |

- Shared graph SHA-256:
  `49630e23374f0375cb7d019ea024bcd5ea0c284feb8dc124b393b60f6e8d9aa7`.
- Core: twelve scenarios, ninety-one assertions, passed.
- Installed provider: ten scenarios, forty-seven assertions, passed.
- Linear: seven scenarios, thirty-nine assertions, passed.
- Protected host-native MCP collection observed all fifteen prescribed events.
  Disposable issues V26-1464 and V26-1465 were created after exact-marker misses,
  read and updated, linked and read back, uniquely reconciled without a repeat
  create, canceled, and independently read back as canceled.
- The host posted receipt
  `linear-receipt:83350e47e4d2b7fdce4ad6e4d147e60e` on the primary disposable
  issue, binding attestation SHA-256
  `f8371abc6dcc2658dec8fd3cf3114cc3fb26c8bd67f47fe6e2e5436f301b37f9`
  and frozen input SHA-256
  `99fbb638b2e14a9dd11cb3336f65b67896b11c3b59ade5a50055a53790fb0262`.
- An earlier collection used V26-1462 and V26-1463; both were canceled and
  cleanup verified. Its evidence was superseded after review changed packaged
  bytes. No earlier attestation was relabeled for the repaired candidate.
- Checked records were produced by their qualification drivers. Retained Linear
  evidence contains random opaque references, not raw issue IDs or host payloads.
  Offline replay verifies exact evidence binding; it does not independently
  establish collector authorization, external calls, or arbitrary opaque-value
  privacy. Those remain protected-host responsibilities.

## Review outcome

Plan: unanimous alignment, with exact digest and reviewer provenance retained.
Implementation slices: independent spec and quality reviews aligned after the
recorded fixes. Final whole-candidate review is a separate gate; its exact tree
identity and outcome belong in the PR and tracker, not a self-mutating record.

## Compounding outcome

The reusable learning is preserved in `docs/workflow-graph-v1.md`, the README
qualification protocol, and negative regression sensors: separate workflow
semantics, acquisition evidence, convergence, and durable authority; qualify
exact artifacts with independent consumers and freshly bound external receipts.
No additional runtime abstraction or duplicate solution document is needed.

## Remaining finish-line evidence

- Run the required registry checks and complete repository test suite.
- Obtain unanimous final implementation review and passing hosted checks.
- Verify PR merged state and remote default-branch containment before closing
  the delivery; no deployment is part of this finish line.
