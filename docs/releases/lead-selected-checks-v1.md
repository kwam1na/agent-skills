# lead-selected-checks-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every
qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-lead-selected-checks-v1`: `qualifications/product/core-lead-selected-checks-v1.*` | `core-workflows-lead-selected-checks-v1`: `qualifications/core-workflows-lead-selected-checks-v1.json` |
| linear | `linear-lead-selected-checks-v1`: `qualifications/product/linear-lead-selected-checks-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-lead-selected-checks-v1.json` | `qualifications/provider-workflows-lead-selected-checks-v1.json` |
| bootstrap | `bootstrap-lead-selected-checks-v1`: `qualifications/product/bootstrap-lead-selected-checks-v1.*` | `bootstrap-workflows-lead-selected-checks-v1`: `qualifications/bootstrap-workflows-lead-selected-checks-v1.json` |
| run-delivery | `run-delivery-lead-selected-checks-v1`: `qualifications/product/run-delivery-lead-selected-checks-v1.zip` and its release file, the product an adopter installs to run deliveries | none |
| jira | `jira-lead-selected-checks-v1`: `qualifications/product/jira-lead-selected-checks-v1.zip` and its release file, the product a Jira adopter installs | none |
| delivery-views | `delivery-views-lead-selected-checks-v1`, built from the same source and not checked in | none |

The `capture-observations-v1` set is retained as immutable history, along with every earlier set. The current-set
pointers in the qualification tests and the README move to `lead-selected-checks-v1`. `pyproject.toml` keeps its
version, because nothing reads it.

## Runtime

Every product archive carries runtime 0.7.7, unchanged from `capture-observations-v1`, with Node 22.6.0 as the minimum.
Its ten files are byte for byte those of the verified `core-capture-observations-v1` product, each matching the
runtime's own `runtime.json` sha256 list. Only `workflowContentSha256` was re-derived, once per profile, for this
release's workflow bytes. No new runtime build was needed, and an adopter on 0.7.7 keeps 0.7.7.

## Release notes

### New: lead-selected checks

A binding can let the lead choose a candidate's checks from its diff, in place of the affected-checks provider's
answer. It is meant for a provider that over-selects, so that a change to a few files runs most of the repository's
checks and pulls unrelated failures into the queue.

- **Opt in.** `selection: {"lead": true}` in `orchestration-binding/1`. Off when absent: every existing binding checks
  as before, and its records and views are unchanged.
- **Record.** `delivery_state.py --dir <state> select --subject <s> --binding <binding> --head <reviewed head>
  --checks <json or -> --reason "<why>"` writes the queue row's `leadSelection` (`{head, checks, reason,
  recordedAt}`) and journals `check.selected`. Each check has the `alwaysRun` entry shape. The tool refuses a binding
  without the option, any head but the reviewed one, a missing reason, an empty list, an id the binding's own checks
  use, an unknown profile, a timeout above the ceiling, a `cwd` outside the checkout and broken prerequisites. Only
  `select` writes `leadSelection`; `select --subject <s> --clear` drops it.
- **Runner.** While the binding allows it and the selection is for the candidate's reviewed head, `check` and `run`
  run the lead's checks plus `alwaysRun`. No provider runs and `fallback` is not added. A hand-off at a new head drops
  the selection, and a selection whose head no longer matches is ignored with a note, so new code is never checked
  with checks chosen for old code. A lead-selected check failing on the base too still stops the queue as
  `base_broken`.
- **Batches.** A batch runs the union of its members' selections, each check once, only when every member has one;
  otherwise the provider selects for the whole batch, and a note names the members without one. Two members that
  define one check id differently stop the batch with the new `selection_invalid`, which also stops a candidate whose
  selection the binding no longer admits.
- **Shown as the lead's.** The result's `selection.lead` lists each member's selection, head and reason, with empty
  `providers`. Under the option the runner writes each row's `checkSelection` (`{by: "lead", head, reason}` or
  `{by: "provider"}`). The markdown view and the static page say "checks selected by the lead at <head>, not the
  provider · why: <reason>", show a waiting or void selection, and add "checks selected by the lead" to the row's
  stats line. The line map's checking panel says "Checks chosen by the lead, not the usual selection: <reason>". The
  stats carry `checksSelectedBy` once a binding with the option has checked the candidate.
- **The trade-off.** A selection is the lead's judgment, not impact analysis: a check the lead leaves out does not run
  before the merge. The close-out `fullHealth` run on the final base remains the backstop.

### Builds on capture-observations-v1

This set is built from `capture-observations-v1`'s merged tree plus this change. Only the `orchestrate-work` skill and
the `delivery-views` line map changed, with the catalog and profile descriptions that describe them.

### Upgrading

- **No stepping stone.** Every profile in this set is one an earlier set already shipped, and each archive holds the
  same number of entries as its `capture-observations-v1` counterpart, so an install takes this set's archive of its
  own profile directly.
- **Node.** The product runtime still requires Node 22.6 or newer.
- **Compatibility.** Everything is additive. A record written by this set may carry `leadSelection` and
  `checkSelection` on queue rows, which an earlier set's state tool refuses as unknown fields, so roll back only from a
  record without them, or clear them first.

### Qualification

- **Offline records.** The core, provider and bootstrap records were re-earned offline against this set's archives.
  Every scenario passed: 13 of 13 for core, 11 of 11 for provider and 3 of 3 for bootstrap, for both the product and
  the workflow-only records.
- **Linear.**
  - The collector input, `qualifications/product/linear-lead-selected-checks-v1.collector-binding.json` (sha256
    `613a5473ead5da2f716cfa9881f6d233b10ce68fd241f00c950af9043f00b0d5`), was frozen before any live operation from the
    verified `core-lead-selected-checks-v1` archive
    (`56f662554d5c095544e1d299c4b0f48ad7588eebbc16bc45a1d7b2f04d726f14`) and `linear-lead-selected-checks-v1` archive
    (`9fa00be4da28a5773767f7d5e5babf55c263f29cb6e4a653a020cea286847f97`).
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its
    receipt is on the canceled primary issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product
    archives.
- **The jira, run-delivery and delivery-views profiles** ship unqualified, as before:
  - The core qualifier is bound to the core profile and refuses a superset.
  - The core skills these profiles include are byte-identical to those in the qualified `core-lead-selected-checks-v1`.
  - What they add is covered by the corpus's own tests, including the run-delivery suite against this set's runtime.
