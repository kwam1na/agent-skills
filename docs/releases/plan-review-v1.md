# plan-review-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every
qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-plan-review-v1`: `qualifications/product/core-plan-review-v1.*` | `core-workflows-plan-review-v1`: `qualifications/core-workflows-plan-review-v1.json` |
| linear | `linear-plan-review-v1`: `qualifications/product/linear-plan-review-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-plan-review-v1.json` | `qualifications/provider-workflows-plan-review-v1.json` |
| bootstrap | `bootstrap-plan-review-v1`: `qualifications/product/bootstrap-plan-review-v1.*` | `bootstrap-workflows-plan-review-v1`: `qualifications/bootstrap-workflows-plan-review-v1.json` |
| run-delivery | `run-delivery-plan-review-v1`: `qualifications/product/run-delivery-plan-review-v1.zip` and its release file, the product an adopter installs to run deliveries | none |
| jira | `jira-plan-review-v1`: `qualifications/product/jira-plan-review-v1.zip` and its release file, the product a Jira adopter installs | none |
| delivery-views | `delivery-views-plan-review-v1`, built from the same source and not checked in | none |

The `jira-tracker-v1` set is retained as immutable history, along with every earlier set. The current-set pointers in
the qualification tests and the README move to `plan-review-v1`. `pyproject.toml` keeps its version, because nothing
reads it.

## Runtime

Every product archive carries runtime 0.7.4, unchanged from `jira-tracker-v1`, with Node 22.6.0 as the minimum. Its
bytes come from the verified `core-jira-tracker-v1` product, whose ten runtime files match the runtime's own
`runtime.json` sha256 list. Only `workflowContentSha256` was re-derived, once per profile, for this release's workflow
bytes. No new runtime build was needed.

## Release notes

### New: a delivery's plan review in the live view

A plan reviewed through `plan-work`'s unbounded review now reaches the record and the live view, and `run-delivery`
runs that review itself.

- **The plan chapter** gains one line beside the approval, "after N review rounds", with "every lens aligned", "ended
  without alignment" or "still open", and a folded "Plan review (N rounds)" that draws the same round track and cards
  as a story's review: each lens's verdict per round, the findings raised, and each closure with the plan revision
  that closed it.
- **Blocking approvals flag.** An approval marked `blocking` and still `waiting` raises "Approvals are blocking work"
  in the chapter's attention area. Approvals that are merely ahead stay in their fold.
- **The input.** `delivery-plan/1` gains an optional `planReview` (lenses, state, ended reason and the rounds in the
  native round-detail shape, with folds for long reviews), and `approvals` entries gain optional `id` and `blocking`.
  The validator and `check-plan` know both. A plan without them renders exactly as before.
- **`run-delivery plan-review prepare|reduce|end`** runs the review: per-lens clean checkouts and briefs, carried
  findings and resolution pairs, fix commits recorded from the round that closed each finding, and a message when the
  review stops converging. No round count ends a plan review. Rounds are kept in a durable store that every plan build
  reads, and the producer always fits what the plan file holds of them to its caps.
- **`lead-init`** embeds the review and refuses to freeze while it is open. **`lead-setup`** drafts `approvals` and
  `outOfScope` from named plan sections, and **`lead-plan approval`** changes one approval with `lead-plan change`'s
  discipline.

### Builds on jira-tracker-v1

This set is built from `jira-tracker-v1`'s merged tree plus the plan-review change. Only the `delivery-views` and
`run-delivery` skills changed. The core archive differs from `core-jira-tracker-v1` only in its release manifest and
runtime descriptor; every core skill is byte-identical.

### Upgrading

- **No stepping stone.** Every profile in this set is one an earlier set already shipped, and installs at
  `jira-tracker-v1` already know every profile here. The largest archive, `run-delivery`, holds 139 entries, under the
  512-entry limit those installs carry, so an install takes this set's archive of its own profile directly.
- **Node.** The product runtime still requires Node 22.6 or newer.
- **Compatibility.** Everything is additive. A delivery that records no plan review and marks no approval blocking
  sees today's page.

### Qualification

- **Offline records.** The core, provider and bootstrap records were re-earned offline against this set's archives.
  Every scenario passed: 13 of 13 for core, 11 of 11 for provider and 3 of 3 for bootstrap, for both the product and
  the workflow-only records.
- **Linear.**
  - The collector input, `qualifications/product/linear-plan-review-v1.collector-binding.json` (sha256
    `8bc16b67e46eb1d2ef99de529962bfb73f13d8fa4c46ab889b2342ce749bcd49`), was frozen before any live operation from the
    verified `core-plan-review-v1` archive (`35bc4b90e2fc395ecff063a51c331e12f966494f522e87eb38d11c6108e5f8f5`) and
    `linear-plan-review-v1` archive (`fbf16c2a81de7155f6b939ea9da7043a87a14bd6f86de030828a2cd8b490488d`).
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its
    receipt is on the canceled primary issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product
    archives.
- **The jira, run-delivery and delivery-views profiles** ship unqualified, as before:
  - The core qualifier is bound to the core profile and refuses a superset.
  - The core skills these profiles include are byte-identical to those in the qualified `core-plan-review-v1`.
  - What they add is covered by the corpus's own tests, including this change's plan-contract, projection, page and
    `plan-review` command suites.
