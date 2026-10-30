# capture-observations-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every
qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-capture-observations-v1`: `qualifications/product/core-capture-observations-v1.*` | `core-workflows-capture-observations-v1`: `qualifications/core-workflows-capture-observations-v1.json` |
| linear | `linear-capture-observations-v1`: `qualifications/product/linear-capture-observations-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-capture-observations-v1.json` | `qualifications/provider-workflows-capture-observations-v1.json` |
| bootstrap | `bootstrap-capture-observations-v1`: `qualifications/product/bootstrap-capture-observations-v1.*` | `bootstrap-workflows-capture-observations-v1`: `qualifications/bootstrap-workflows-capture-observations-v1.json` |
| run-delivery | `run-delivery-capture-observations-v1`: `qualifications/product/run-delivery-capture-observations-v1.zip` and its release file, the product an adopter installs to run deliveries | none |
| jira | `jira-capture-observations-v1`: `qualifications/product/jira-capture-observations-v1.zip` and its release file, the product a Jira adopter installs | none |
| delivery-views | `delivery-views-capture-observations-v1`, built from the same source and not checked in | none |

The `plan-review-v1` set is retained as immutable history, along with every earlier set. The current-set pointers in
the qualification tests and the README move to `capture-observations-v1`. `pyproject.toml` keeps its version, because
nothing reads it.

## Runtime

Every product archive carries runtime 0.7.7, with Node 22.6.0 as the minimum. Every earlier set from
`orchestration-v1` to `plan-review-v1` carried 0.7.4, so an adopter already running 0.7.7 could not install any of them
without rolling its runtime back.

- **Source.** `kwam1na/agent-delivery-harness` commit `32da34fae`, whose kernel and CLI packages are 0.7.7. The runtime
  was built from a clean checkout of that commit with the harness's own `product:build-runtime` script, once per
  profile against that profile's workflow-only release manifest, so each descriptor's `workflowContentSha256` is
  derived by the producer for that profile's workflow bytes.
- **Verification.** Every one of the ten runtime files in every product archive matches its descriptor's sha256 list,
  and each descriptor's file list is identical to the one in an adopter's verified generation already running 0.7.7
  from the same commit. The runtime components are byte-identical across profiles.
- **What changed from 0.7.4.** Versions 0.7.5 to 0.7.7 add cheap scoped preparation and
  scoped review evidence, concurrent scoped checks with each snapshot setup built once, the scoped plan passed to
  declared checks during gate and release, reuse of full Git checks across record-neutral staging, single-traversal
  snapshot verification, batched native Git source reads in binary transport over bounded object waves, and progress
  observations taken from each command's own fresh candidate capture instead of an extra scan.

## Release notes

### Same workflows, newer runtime

This set is built from `plan-review-v1`'s merged tree. No workflow, skill or schema changed: every profile's
`workflowContentSha256` equals its `plan-review-v1` value, and each product archive differs from its `plan-review-v1`
counterpart only in its release manifest and these runtime files: `runtime.json`, `cli.mjs`, `cli-api.mjs`,
`cli-api.d.mts`, `kernel.mjs` and `kernel.d.mts`. `policy.mjs`, the loader, bootstrap and license notices are
unchanged.

### Upgrading

- **No stepping stone.** Every profile in this set is one an earlier set already shipped, and each archive holds the
  same number of entries as its `plan-review-v1` counterpart, so an install takes this set's archive of its own profile
  directly.
- **Node.** The product runtime still requires Node 22.6 or newer.
- **Compatibility.** An adopter on 0.7.4 gains the 0.7.7 behaviour above. Rolling back to an earlier set selects
  runtime 0.7.4 again; keep a reader that understands 0.7.7 observations for retained run history.

### Qualification

- **Offline records.** The core, provider and bootstrap records were re-earned offline against this set's archives.
  Every scenario passed: 13 of 13 for core, 11 of 11 for provider and 3 of 3 for bootstrap, for both the product and
  the workflow-only records.
- **Linear.**
  - The collector input, `qualifications/product/linear-capture-observations-v1.collector-binding.json` (sha256
    `9dcb799ed76e1eab19eff285afaa41d451ceb877e33378b06e3497d70ef12c18`), was frozen before any live operation from the
    verified `core-capture-observations-v1` archive
    (`7641253605b00c270baa8ef4f83f5e0871ec85a18af41e9ef5faccb79ac5f0e2`) and `linear-capture-observations-v1` archive
    (`d210689d0b4d30e5a31f182ea5825d3b640167208c8d454af03eca7ef321c136`).
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its
    receipt is on the canceled primary issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product
    archives.
- **The jira, run-delivery and delivery-views profiles** ship unqualified, as before:
  - The core qualifier is bound to the core profile and refuses a superset.
  - The core skills these profiles include are byte-identical to those in the qualified `core-capture-observations-v1`.
  - What they add is covered by the corpus's own tests, including the run-delivery suite against this set's runtime.
