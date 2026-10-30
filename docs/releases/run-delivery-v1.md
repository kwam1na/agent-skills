# run-delivery-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every
qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-run-delivery-v1`: `qualifications/product/core-run-delivery-v1.*` | `core-workflows-run-delivery-v1`: `qualifications/core-workflows-run-delivery-v1.json` |
| linear | `linear-run-delivery-v1`: `qualifications/product/linear-run-delivery-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-run-delivery-v1.json` | `qualifications/provider-workflows-run-delivery-v1.json` |
| bootstrap | `bootstrap-run-delivery-v1`: `qualifications/product/bootstrap-run-delivery-v1.*` | `bootstrap-workflows-run-delivery-v1`: `qualifications/bootstrap-workflows-run-delivery-v1.json` |
| run-delivery | `run-delivery-run-delivery-v1`: `qualifications/product/run-delivery-run-delivery-v1.zip` and its release file, the product an adopter installs | none |
| delivery-views | `delivery-views-run-delivery-v1`, built from the same source and not checked in | none |

The `review-evidence-v1` set is retained as immutable history, along with every earlier set. The current-set pointers
in the qualification tests and the README move to `run-delivery-v1`. `pyproject.toml` keeps its version, because
nothing reads it.

## Runtime

Every product archive carries runtime 0.7.4, unchanged from `review-evidence-v1`, with Node 22.6.0 as the minimum. Its
bytes come from the verified `core-review-evidence-v1` product: all ten files were checked against the runtime's own
`runtime.json` sha256 list, and all matched. Only `workflowContentSha256` was re-derived, once per profile, for this
release's workflow bytes. That includes the new `run-delivery` product, whose descriptor is bound to the `run-delivery`
projection. No new runtime build was needed.

## Release notes

### New: run-delivery

`run-delivery` is a non-core skill, shipped in a new `run-delivery` profile that includes `delivery-views`. The `linear`
profile does not include it.

It carries out an orchestrated delivery as commands, run from the installed generation as
`.agent-skills/current/skills/run-delivery/scripts/run_delivery.py`:

- **Lanes:** lens round briefs and round files, with each result checked against its brief; gate runs recorded against
  the head; in-app checks under an exclusive lease, with before/after video evidence where a story asks for it; pushes
  and draft change requests only under the delivery's written authority; and hand-offs that refuse unless the review,
  gates and in-app evidence are current at the handed head.
- **The lead:** setup from the approved plan, record initialization, the lane brief, assignment, acceptance that checks
  every hand-off claim, cleanup, the reconcile sweep, the evidence page, a product-only ship branch, and close-out with an
  archive of the record and the lead-side logs.
- **Release-relative:** the tool finds its sibling skills and its release identity from the generation it runs from,
  and refuses to write a brief when the repository's receipt doesn't name that generation.
- **Host-neutral:** no command calls a host API, and every host behaviour has a path for a host without it.
- **Forge adapters:** reference Bitbucket and GitHub adapters behind the forge contract. Their only write is opening a
  change request as a draft; neither prints a token.
- **`doctor`**, the setup check: one line per prerequisite, `ok`, `missing`, `wrong` or `skipped`, a fix on every line
  that fails, and one failing line per missing prerequisite.
- **The adoption guide**, `references/adopting.md`: an empty repository reaches a clean `doctor` and a closed delivery by
  running only its marked blocks, which the suite does.
- **Earlier names:** `.agents/flow.json` and the `DELIVERY_FLOW_*` variables are still read when the new names are
  absent, and `doctor` reports them. The frozen schema names `delivery-flow/1` and `delivery-flow-delivery/1` are kept.
- POSIX only: on Windows the tool exits with a message saying so.

### New: the plan chapter

The live line map can now say whether a delivery is still what was agreed, and what proves it.

- **The input, `delivery-plan/1`.** One JSON document at `<state directory>/plan.json`, at most 1 MiB, holding:
  - the outcome, the units and their criteria, and the proofs each unit owes;
  - the finish lines and what each covers;
  - the approved snapshot, the changes since, and producer evidence.

  Its schema ships as `delivery-views/references/delivery-plan.schema.json`. `views_common.py check-plan` checks a
  file, naming the failing key path without echoing any value.
- **The renderer** embeds a valid plan within a fixed 512 KiB share of the page, clipping the oldest changes' text
  first and never the approved snapshot or any count. An unreadable plan renders as if absent, plus one flag naming
  the key path. With no plan, every page is unchanged.
- **The chapter, The plan:**
  - It opens with only what needs the engineer: how many criteria are proven, the outcome, eight flags in a fixed
    order, the units not yet proven with each one's next missing proof, and finish-line meters.
  - Everything else is folded behind counts, with an "as approved / as amended" switch, a scope-over-time chart and
    word redlines.
  - Record proofs (`handed`, `merged`, `review`) are derived from every record the page draws, so the chapter redraws
    live. A plan write moves the record and shows open pages a reload notice.
- **The producer, `run-delivery`:**
  - It writes `plan.json` when a delivery's stories declare criteria. Only lead-side commands and the sweep write it,
    under one lock, atomically and only when it changed.
  - Its sources are the delivery file, an append-only changes log, the in-app and video flow logs, and a new acceptance
    log.
  - `lead-plan change` records each change to the approved plan with what it said before and after, and commits the
    delivery file. `lead-plan why` gives a change its reason.
  - A story registered mid-delivery is recorded as `added`, and an edit made by hand shows as `unrecorded`.
  - `doctor` gains a plan row.

### Builds on review-evidence-v1

This set is built from the merged tree of this delivery and `review-evidence-v1`. Everything that release describes
ships here unchanged: runless review observations, evidence bound to the reviewed candidate, merge occurrence,
`{checkMode}`, compact closure evidence, `--pass-fd`, and the FSL-1.1-ALv2 license. The plan chapter's record proofs
read the same review evidence: an observation of the queue item's head counts, and it stays bound to that reviewed
head after a runner sync, so a synced or merged unit keeps its review proof.

### Upgrading

- **No stepping stone.** `review-evidence-v1` and `delivery-views-v1` installs already carry the 512-entry limit. The
  largest archive in this set, `run-delivery`, holds 138 entries, so an install at either set takes this one directly.
- **Node.** The product runtime still requires Node 22.6 or newer.
- **Compatibility.** Everything is additive:
  - A repository that declares no `run-delivery` configuration and no `plan.json` sees no change in its views.
  - A delivery file without `criteria` gets no plan.
  - Merge hooks that ignore the check mode keep working.

### Qualification

- **Offline records.** The core, provider and bootstrap records were re-earned offline against this set's archives.
  Every scenario passed: 13 of 13 for core, 11 of 11 for provider and 3 of 3 for bootstrap. Each replays byte for byte.
- **Linear.**
  - The collector input, `qualifications/product/linear-run-delivery-v1.collector-binding.json` (sha256
    `9284c709d14c287be7cecf74a4592a8ae06c5e2ca7602ffd96f092e348010e08`), was frozen before any live operation from the
    verified `core-run-delivery-v1` archive (`08610e46436ad0239f1171dd489bc1cbeb342fc01a9ce64d7e50920e95f5b0cb`) and
    `linear-run-delivery-v1` archive (`9d75dd6f007fdaa2c830c4e2f14176c0844e30df5f5e3a91451da605b44bc949`).
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its
    receipt is on the canceled primary issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product
    archives.
- **The run-delivery and delivery-views profiles** ship unqualified, as the delivery-views profile did before:
  - The core qualifier is bound to the core profile and refuses a superset.
  - The core skills these profiles include are byte-identical to those in the qualified `core-run-delivery-v1`.
  - What they add is covered by the corpus's own tests: the run-delivery characterization, behaviour, setup-check and
    guide-driven fixture adopter suites, and the delivery-views golden, page-lane, renderer, plan and snapshot suites.
