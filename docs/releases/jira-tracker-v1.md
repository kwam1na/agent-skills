# jira-tracker-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every
qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-jira-tracker-v1`: `qualifications/product/core-jira-tracker-v1.*` | `core-workflows-jira-tracker-v1`: `qualifications/core-workflows-jira-tracker-v1.json` |
| linear | `linear-jira-tracker-v1`: `qualifications/product/linear-jira-tracker-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-jira-tracker-v1.json` | `qualifications/provider-workflows-jira-tracker-v1.json` |
| bootstrap | `bootstrap-jira-tracker-v1`: `qualifications/product/bootstrap-jira-tracker-v1.*` | `bootstrap-workflows-jira-tracker-v1`: `qualifications/bootstrap-workflows-jira-tracker-v1.json` |
| run-delivery | `run-delivery-jira-tracker-v1`: `qualifications/product/run-delivery-jira-tracker-v1.zip` and its release file, the product an adopter installs to run deliveries | none |
| jira | `jira-jira-tracker-v1`: `qualifications/product/jira-jira-tracker-v1.zip` and its release file, the product a Jira adopter installs | none |
| delivery-views | `delivery-views-jira-tracker-v1`, built from the same source and not checked in | none |

The `run-delivery-v1` set is retained as immutable history, along with every earlier set. The current-set pointers in
the qualification tests and the README move to `jira-tracker-v1`. `pyproject.toml` keeps its version, because nothing
reads it.

The `jira` product is checked in for the same reason the `run-delivery` product is: it is what an adopter installs, so
the release channel must carry it. `delivery-views` is not, because the `run-delivery` and `jira` products both include
it.

## Runtime

Every product archive carries runtime 0.7.4, unchanged from `run-delivery-v1`, with Node 22.6.0 as the minimum. Its
bytes come from the verified `core-run-delivery-v1` product: all ten files were checked against the runtime's own
`runtime.json` sha256 list, and all matched. Only `workflowContentSha256` was re-derived, once per profile, for this
release's workflow bytes. That includes the new `jira` product, whose descriptor is bound to the `jira` projection. No
new runtime build was needed.

## Release notes

### New: the Jira profile

`jira` is a new optional profile layered on `core` and `delivery-views`. It mirrors the `linear` profile: one adapter
and two tracking workflows that reach the tracker only through it. Neither the `linear` nor the `run-delivery` profile
includes it.

- **`jira-tracker-adapter`** maps the neutral tracker operations onto Jira through the host's Atlassian MCP connector,
  and through nothing else. Its contract, `references/adapter-contract.json`, lists every allowed host operation and
  rejects any other access path: direct endpoints, SDKs, HTTP clients and commands.
- **`create-jira-ticket`** and **`execute-jira-ticket`** decompose work into tickets and carry a ticket through tracked
  execution, using the neutral operations through the adapter alone.
- **Configuration.** The adopting repository declares its tracker properties in `.agents/tracker-properties.json`,
  which the adapter reads and never writes. Beside `project`, `team` and `statuses`, its `jira` block names:
  - `site`: the Atlassian site every operation targets, by cloud identifier or site address;
  - `childIssueType`: the issue type for an item under a parent that is not an epic;
  - `standaloneIssueType`: the issue type for an item under an epic or with no parent;
  - `dependencyLinkType`: the issue link type that records a true blocker;
  - optionally `summaryPrefix`, `components` and `labels`, which every created item carries;
  - optionally `canceledResolutions`, the resolutions that mean an item was abandoned rather than finished.

  A named issue type or link type the tracker doesn't define is surfaced as a mismatch, never substituted.
- **Status categories.** `list-work` folds each item's status category into the normalized four: `new` becomes
  `backlog`, `indeterminate` becomes `started`, and `done` becomes `completed`, or `canceled` when the item's
  resolution is one `canceledResolutions` declares. Any other category makes the result `malformed`, never a guess.
- **Links are read back.** `link-dependencies` creates one `dependencyLinkType` link per true blocker, then reads every
  link back and confirms the blocked item shows the blocker on the link type's inward side before it reports success.
- **Transitions go straight to the declared status.** `update-status` and `close-or-handoff` take the transition whose
  target is the declared status name. When no available transition reaches it, the adapter surfaces the mismatch
  instead of stepping through other statuses.
- **No silent duplicates.** A mutation is applied at most once per invocation. After a timeout or an ambiguous result,
  only reconciliation runs, and anything short of one exact match returns `reconciliation-required` and stops.
- **Optional.** When the connector is absent, unconfigured or blocked, the adapter returns the neutral actionable
  outcome, and an optional tracking step continues on the complete core path.

The release and receipt code and the receipt and release-manifest schemas now accept `jira` as a profile, and the
catalog lists it in the shared skills' profile lists. That code and those schemas ship in every archive, which is why
every profile's archive changes in this set.

### Builds on run-delivery-v1

This set is built from `run-delivery-v1`'s merged tree plus the Jira profile. Everything that release describes ships
here unchanged: the `run-delivery` commands, setup check and adoption guide, the plan chapter, and the review evidence
it builds on. No skill outside the Jira profile changed: the core archive differs from `core-run-delivery-v1` only
in the release and receipt code, the two schemas, and its manifest and runtime descriptor.

### Upgrading

- **No stepping stone for the profiles you have.** `run-delivery-v1`, `review-evidence-v1` and `delivery-views-v1`
  installs already carry the 512-entry limit. The largest archive in this set, `run-delivery`, holds 138 entries, and
  `jira` holds 135, so an install at any of those sets takes this set's archive of its own profile directly.
- **Moving an existing install to `jira` takes one step first.** An installed product verifies an incoming release
  with its own code, and products before this set don't know the `jira` profile: they refuse its archive with
  `release.identity: unsupported profile`. Install this set's archive of the current profile first, then the `jira`
  archive. A fresh install is verified by the archive's own launcher and needs no stepping stone.
- **Node.** The product runtime still requires Node 22.6 or newer.
- **Compatibility.** Everything is additive. A repository that installs no `jira` profile sees no change, and the
  `jira` block of `.agents/tracker-properties.json` is read only by the Jira adapter.

### Qualification

- **Offline records.** The core, provider and bootstrap records were re-earned offline against this set's archives.
  Every scenario passed: 13 of 13 for core, 11 of 11 for provider and 3 of 3 for bootstrap. Each replays byte for byte,
  as do their workflow-only records.
- **Linear.**
  - The collector input, `qualifications/product/linear-jira-tracker-v1.collector-binding.json` (sha256
    `3fc06a47bd4f8313d678e698f88518e64ff7615eb14ac47ecfb0ca1fe66c0e35`), was frozen before any live operation from the
    verified `core-jira-tracker-v1` archive (`b0e62ce335ee77187b843b6e58f71540b1b7bffe364ea6d80695a57dc47e12d1`) and
    `linear-jira-tracker-v1` archive (`5a178fddb311c766902d0fef1602acb97d00a881ba929db6cc1e78d891308feb`).
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its
    receipt is on the canceled primary issue and on the release's tracking issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product
    archives.
- **The jira, run-delivery and delivery-views profiles** ship unqualified, as run-delivery and delivery-views did
  before:
  - The core qualifier is bound to the core profile and refuses a superset.
  - The core skills these profiles include are byte-identical to those in the qualified `core-jira-tracker-v1`.
  - What they add is covered by the corpus's own tests: for `jira`, the adapter-contract, status-fold, redaction,
    fail-closed, transport-boundary, tracking-workflow and archive suites; for the others, the suites
    `run-delivery-v1` names.
  - No live Jira host run qualifies the Jira adapter in this set.
