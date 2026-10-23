# delivery-views-v1

## Version and set names

A release is identified by its release ids and qualification set names, so this release is a new themed set for every qualified profile:

| Profile | Product release id and checked set | Workflow-only release id and record |
| --- | --- | --- |
| core | `core-delivery-views-v1`: `qualifications/product/core-delivery-views-v1.*` | `core-workflows-delivery-views-v1`: `qualifications/core-workflows-delivery-views-v1.json` |
| linear | `linear-delivery-views-v1`: `qualifications/product/linear-delivery-views-v1.*` | none |
| provider | qualified against the core product archive: `qualifications/product/provider-delivery-views-v1.json` | `qualifications/provider-workflows-delivery-views-v1.json` |
| bootstrap | `bootstrap-delivery-views-v1`: `qualifications/product/bootstrap-delivery-views-v1.*` | `bootstrap-workflows-delivery-views-v1`: `qualifications/bootstrap-workflows-delivery-views-v1.json` |
| delivery-views | `delivery-views-delivery-views-v1`, built from the same source and not checked in | none |

The `orchestration-v1` set is retained as immutable history, along with every earlier set. The current-set pointers in the qualification tests and the README move to `delivery-views-v1`. `pyproject.toml` stays at 0.1.0, because nothing reads it.

## Runtime

The product archives carry runtime 0.7.4, unchanged from `orchestration-v1`. Its bytes come from an installed and verified generation. Every file was checked against the runtime's own `runtime.json` sha256 list, and all ten matched. Only `workflowContentSha256` was re-derived, once per profile, for this release's workflow bytes.

## Release notes

### New: delivery-views

`delivery-views` is a non-core skill, shipped in the `linear` profile and in a new `delivery-views` profile.

A repository that delivers epics through orchestrate-work gets ready-made views and snapshot commands by writing configuration, with no view code of its own. The configuration is `delivery-views-config/1`, in `.agents/delivery-views.json`, and the views and commands are declared in the binding's `state`. The skill's guide covers adoption, with complete Jira and Bitbucket, and Linear and GitHub, examples.

**Live line map** (`scripts/render.py line-map`):
- The delivery is told in six chapters, with a detail panel for each piece of work.
- Each piece's run is a track of review rounds, with a card for the picked round. The card shows each finding by severity, what became of it and the fix that closed it, and each lens's brief and result as stored.
- A light/dark theme is kept per repository.
- The page opts in to the live stream and refreshes its run data after each push.

**Delivery history page** (`scripts/render.py history`):
- It lists every run the configured `runs` command shows, read with `runs show`, and every export in the configured directory that no ignore pattern skips. Older telemetry records are listed by branch.
- Rows are newest first. Each one opens the same run card as the line map and keeps every round the run closed.
- It links back to the binding's line map, as a file and at its `/view/` address.

**Runs:**
- Runs are read through the configured `runs` command and read six at a time.
- Each command is bounded, and all of a render's reads share one deadline, so a slow or hanging run holds only its own slot.
- Results are cached in the state directory, within a 4 MiB page budget.

**Snapshot commands** (`scripts/snapshot.py --kind jira|bitbucket|linear|github`), each printing a `work-snapshot/1`:
- Credentials come only from named environment variables and go only to the provider's origin.
- Each command is bounded by one deadline and retries only 429 and 5xx responses.
- A command fails closed, on one stderr line that never holds a credential.
- The forge kinds match pull requests to the record's subjects.

**The served record:**
- `render.py` and `snapshot.py` take `--state-dir`, which names the record being drawn over the binding's `state.directory`.
- A repository serving several records passes `--state-dir {stateDirectory}`, so each view reads, and caches runs for, the record served.

**Links** come from configured URL templates. Values are percent-encoded, and a link is used only when it stays on the template's origin.

**Round reports** use a new neutral format, `review-round-report/1`, which includes an optional `briefs` member.

**Adopter proof:** an outside-adopter fixture uses Jira and Bitbucket, with configuration only. It installs a built release and validates its binding. It takes both snapshots from recorded responses and renders the line map and the history page from the installed release.

### orchestrate-work

- The binding names which renderer the live server shows at its front page (`liveHome`).
- Delivery state links each candidate to its run: `handoff --run`, and `runId` on queue and planned elements.
- `{stateDirectory}`, beside `{parent}`, is the resolved state directory a command runs for. It is substituted in renderer argv (render and live) and in tracker, forge and work-hints snapshot argv (including serve's refresh and `next`).
- The live view serves `/view/` outputs whose names need percent-encoding. A segment that decodes to a separator, `.` or `..`, NUL, invalid UTF-8 or further percent-encoding is a 404.

### Review workflows

Every review lens ends its result with approval conditions: for each finding, whether its proposed change, adopted as written, resolves it. Follow-up rounds verify the adopted changes and the changed text. Plan review raises a missed P0 or P1 against unchanged text; delivery review keeps its out-of-delta rule.

### Release tooling

- `MAX_ENTRIES` rises from 128 to 512; byte bounds are unchanged.
- Release tooling loads profile documents by walking `includes`, and refuses a missing included profile.
- `delivery-views` is a supported profile.
- The Linear qualification's transport sensor no longer scans `skills/delivery-views/`. It guards how the workflow's tracker operations reach Linear (host-native only), and the views are a separate configured skill.

### Upgrading

- **Stepping stone.** An installed product verifies an incoming release against its own limits, so a release that raises a limit must itself fit the old one. An install whose product allows 128 entries cannot take this set's linear archive (134 entries) directly. It first installs a release of 128 entries or fewer that carries the 512 limit: the linear product built at the commit that raised the limit is 127 entries. It then installs this set. A fresh install is verified by the archive's own launcher and needs no stepping stone.
- **Ordering with `{stateDirectory}`.** The stepping stone predates `{stateDirectory}` and `--state-dir`. A binding that uses either must wait for this set.
- **Node.** The product runtime still requires Node 22.6 or newer. delivery-views adds no Node requirement: its renderers and snapshot commands are stdlib Python, and its pages run in the browser.
- **Compatibility.** Everything is additive. A repository that declares no delivery-views renderer or snapshot command sees no change. A binding that uses no `{stateDirectory}` gets the same argv as before.

### Qualification

- **Offline records:** the core, provider and bootstrap records were re-earned offline against this set's archives, and each replays byte for byte.
- **Linear:**
  - The collector input was frozen before any live operation.
  - The attestation comes from the owner-approved live host run on two disposable issues, both canceled at the end. Its receipt is on the canceled primary issue and on the release's tracking issue.
  - `qualify-linear.py` passes all seven scenarios against the frozen input and the exact core and linear product archives.
- **delivery-views profile:** this product ships unqualified, for two reasons.
  - The core qualifier is bound to the core profile and refuses a superset. The profile's included core is the qualified `core-delivery-views-v1`.
  - What the views add is covered by the corpus's own tests (golden, page-lane, renderer, snapshot and outside-adopter suites), not by a qualification record.
