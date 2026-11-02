# draft-building-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every
qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-draft-building-v1`: `qualifications/product/core-draft-building-v1.*` | `core-workflows-draft-building-v1`: `qualifications/core-workflows-draft-building-v1.json` |
| linear | `linear-draft-building-v1`: `qualifications/product/linear-draft-building-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-draft-building-v1.json` | `qualifications/provider-workflows-draft-building-v1.json` |
| bootstrap | `bootstrap-draft-building-v1`: `qualifications/product/bootstrap-draft-building-v1.*` | `bootstrap-workflows-draft-building-v1`: `qualifications/bootstrap-workflows-draft-building-v1.json` |
| run-delivery | `run-delivery-draft-building-v1`: `qualifications/product/run-delivery-draft-building-v1.zip` and its release file, the product an adopter installs to run deliveries | none |
| jira | `jira-draft-building-v1`: `qualifications/product/jira-draft-building-v1.zip` and its release file, the product a Jira adopter installs | none |
| delivery-views | `delivery-views-draft-building-v1`, built from the same source and not checked in | none |

The `lead-selected-checks-v1` set is retained as immutable history, along with every earlier set. The current-set
pointers in the qualification tests and the README move to `draft-building-v1`. `pyproject.toml` keeps its
version, because nothing reads it.

## Runtime

Every product archive carries runtime 0.7.7, unchanged from `lead-selected-checks-v1`, with Node 22.6.0 as the minimum.
Its ten files are byte for byte those of the verified `core-lead-selected-checks-v1` product, each matching the
runtime's own `runtime.json` sha256 list. Only `workflowContentSha256` was re-derived, once per profile, for this
release's workflow bytes. No new runtime build was needed, and an adopter on 0.7.7 keeps 0.7.7.

## Release notes

### Changed: draft change requests move planned work to building

Forge reconcile used to link a draft change request and do nothing else, so a delivery with a forge snapshot and no
tracker snapshot showed a story as Not started until hand-off, though every lane's pull request opens as a draft.

- **Draft.** A draft change request now moves work at `planned` to `building`.
- **Open.** An open change request still moves `planned` or `building` work to review.
- **Kept stages.** Building, review and queued work keep their stage on a draft.

### Builds on lead-selected-checks-v1

This set is built from `lead-selected-checks-v1`'s merged tree plus this change. Only the `orchestrate-work` skill and
one line of the `run-delivery` skill changed.

### Upgrading

- **No stepping stone.** Every profile in this set is one an earlier set already shipped, and each archive holds the
  same number of entries as its `lead-selected-checks-v1` counterpart, so an install takes this set's archive of its
  own profile directly.
- **Node.** The product runtime still requires Node 22.6 or newer.
- **Compatibility.** No record shape changed, so a rollback to `lead-selected-checks-v1` reads every record this set
  writes. An earlier set's reconcile leaves a story already at `building` where it is.

### Qualification

- **Offline records.** The core, provider and bootstrap records were re-earned offline against this set's archives.
  Every scenario passed: 13 of 13 for core, 11 of 11 for provider and 3 of 3 for bootstrap, for both the product and
  the workflow-only records.
- **Linear.**
  - The collector input, `qualifications/product/linear-draft-building-v1.collector-binding.json` (sha256
    `6b022864a2cca740ecc84be2493c00d89a362c46f797a348cd5e062a80a69ac5`), was frozen before any live operation from the
    verified `core-draft-building-v1` archive
    (`51cd9926d94ab1ef6623c74fd7cb5d2324c9868029ca2e4001d8cd389fb43df0`) and `linear-draft-building-v1` archive
    (`b11c63362206988e81ce3ce355ccc74082ed0b23b9208c7334ee0c9e0587aca1`).
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its
    receipt is on the canceled primary issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product
    archives.
- **The jira, run-delivery and delivery-views profiles** ship unqualified, as before:
  - The core qualifier is bound to the core profile and refuses a superset.
  - The core skills these profiles include are byte-identical to those in the qualified `core-draft-building-v1`.
  - What they add is covered by the corpus's own tests, including the run-delivery suite against this set's runtime.
