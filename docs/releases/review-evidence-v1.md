# review-evidence-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-review-evidence-v1`: `qualifications/product/core-review-evidence-v1.*` | `core-workflows-review-evidence-v1`: `qualifications/core-workflows-review-evidence-v1.json` |
| linear | `linear-review-evidence-v1`: `qualifications/product/linear-review-evidence-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-review-evidence-v1.json` | `qualifications/provider-workflows-review-evidence-v1.json` |
| bootstrap | `bootstrap-review-evidence-v1`: `qualifications/product/bootstrap-review-evidence-v1.*` | `bootstrap-workflows-review-evidence-v1`: `qualifications/bootstrap-workflows-review-evidence-v1.json` |
| delivery-views | not built for this set | none |

The `delivery-views-v1` set is retained as immutable history, along with every earlier set. The current-set pointers in the qualification tests and the README move to `review-evidence-v1`.

## Runtime

The product archives carry runtime 0.7.4, unchanged from `delivery-views-v1`. Its bytes come from the verified `core-delivery-views-v1` archive. Every file was checked against the runtime's own `runtime.json` sha256 list, and all ten matched; the runtime files are byte-identical across the core, linear and bootstrap products. Only `workflowContentSha256` was re-derived, once per profile, for this release's workflow bytes.

## Release notes

### orchestrate-work

- **Review observations without a product run.** `delivery_state.py capture-review` keeps a lane's actual obtain-review outcomes, one file per round, after hand-off. The original bytes are stored in a digest-addressed `review-artifacts/` file and a `review.evidence` journal observation is appended. Repeating a capture or an exact prefix appends nothing; rewritten prior rounds are refused. The capture never creates a run, backdates a round or supplies admission evidence.
- **Evidence follows the reviewed candidate.** The final candidate reference must match the retained queue head, the reviewed candidate rather than the runner's synced head. A runner sync onto a moved base keeps the evidence; a new hand-off does not.
- **Merge occurrence.** Recovery preserves the source merge time, and terminal candidates stay bound to their pull request.
- **`{checkMode}`.** The `ready` and `merge` hooks invoked by `Runner.merge` can take `{checkMode}`: `single` without a batch-synced head, `batch` with one. It identifies how the candidate was checked; it is not a freshness proof.
- `review.evidence` has a tracker-update instruction (none: the lane already posted each round).

### delivery-views

- A runless lane shows its captured native review rounds in the same run cards. Missing, damaged or stale evidence says so instead of showing approval, and a live push that changes the candidate invalidates a retained approval at once.
- Compact reviewer cards show closure evidence.
- Each detail panel shows a stage-specific time ("Checking since", "Handed over", "Landed"), with its basis in the accessible name.
- Pull-request links are labelled by number, with the repository carried in the link target and its accessible name. Cross-repository forge links resolve through the configured routes.
- The page carries no native tooltips; full identities are in each element's `aria-label`.
- A malformed record with a null queue or non-list lanes renders instead of failing.

### Product launcher

- On POSIX, a caller can lend explicit pipe or socket descriptors to a native command with `--pass-fd`, discovered through `harness --capabilities`. See [product generations](../product-generation.md).

### Licensing

- The corpus is licensed under FSL-1.1-ALv2, matching the harness.

### Upgrading

- **From `delivery-views-v1`.** The archives fit the 512-entry limit that `delivery-views-v1` carries, so an install at that set takes this one directly. An install older than `delivery-views-v1` follows that set's stepping-stone guidance first.
- **Compatibility.** Everything is additive. A binding that uses no `{checkMode}` and captures no runless review evidence sees no change in behaviour.

### Qualification

- **Offline records:** the core, provider and bootstrap records, product and workflow-only, were re-earned offline against this set's archives.
- **Linear:**
  - The collector input was frozen before any live operation.
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its receipt is on the canceled primary issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product archives.
