# Product generations

A release may carry a `runtime/` payload alongside its selected portable workflow
profile. The existing release manifest covers both, and the lifecycle retains,
verifies, switches, and rolls them back as one generation. No separate runtime
pointer or maintenance journal is introduced.

`product-runtime/` holds the runtime source. `scripts/cut-release.py` copies it per
profile, rewrites only `workflowContentSha256`, and supplies that copy as
`--runtime-directory` to the same build `scripts/build-release.py build` runs. That directory contains `runtime.json`, the CLI/kernel/policy JavaScript
bundles, matching kernel and CLI API declarations, loader/bootstrap, and license
notices. The descriptor's
`workflowContentSha256` must match the exact workflow-only release projection;
its file list must match the runtime bytes. An incompatible workflow projection
or altered bundle blocks the build and verifier. The complete archive digest
belongs to the active receipt, not to an embedded runtime constant.

Release ZIPs are executable Python archives. After authenticating distributed
bytes through the release channel, an ordinary adopter can invoke:

```sh
python3 -B product.zip --root /repo --product apply --archive product.zip --metadata product.json --maintenance
python3 -B /repo/.agent-skills/current --root /repo harness check
python3 -B /repo/.agent-skills/current --root /repo --product status
python3 -B /repo/.agent-skills/current --root /repo --product rollback --maintenance
python3 -B /repo/.agent-skills/current --root /repo --product recover --maintenance
```

Use the same `apply` command with the next archive to advance a generation; it
selects install, update, or no-op from verified lifecycle state. Both host skill
exposures and the runtime resolve through the existing current generation.
The Node runtime can load erasable TypeScript consumer configuration without
consumer npm dependencies, including imports of the bundled kernel.

An installed generation must continue to match its receipt exactly. Run the
installed Python entry point with `-B`, as above, so Python does not write cache
files into that immutable tree. If stray `__pycache__/*.pyc` files appear,
`status` reports the remediable `local-bytecode-divergence` blocker with the
`remove-python-bytecode` recovery step, and lifecycle updates refuse with the
same blocker until an operator removes those bytecode files. Other unexpected
files and modifications to receipt-tracked files remain `local-divergence` and
require review of the managed diff.

`--product` requires a runtime and checks policy reconciliation as well as the
underlying lifecycle. When an adopter has `.agents/policy`, the selected runtime
recompiles its existing snapshot after install, update, rollback, or recovery.
If that fails, the lifecycle may already be current but `productReady` is false;
product status and runtime entry reject that intermediate state. Correct the
reported policy inputs and retry `--product recover`. The snapshot stays
adopter-owned. Product reconciliation updates an already-declared persona archive
digest to the selected generation and validates current policy inputs and
charters. Other provenance and comparison adjudications are preserved.
Runtime commands share the existing lifecycle lock so maintenance cannot switch
the selected generation while a command executes.

On POSIX, a caller can lend explicit pipe or socket descriptors to a native
command without replacing stdin, stdout or stderr. Discover support with
`harness --capabilities`, which returns `agent-skills-harness-capabilities/1`
and `passFileDescriptors`. Then place each `--pass-fd <integer>` before the
runtime command, for example `harness --pass-fd 3 --pass-fd 4 gate`. At most
eight distinct open descriptors above stderr are accepted; regular files and
stdio are refused. The caller owns and closes these channels. The launcher
borrows them only for that command, retains ordinary readiness checks and
cancellation, and assigns no authority to their contents. An unsupported
platform or older launcher requires the caller's ordinary fresh-computation
path; it must not substitute a cached result file.

For the first compile, the adopter supplies
`.agents/policy/repository-policy.json`, `.agents/policy/adapters.json`, and
`.agents/policy/bootstrap-inputs.json`. The bootstrap-inputs document contains
exactly two non-negative safe integers, `productTrustRevocationEpoch` and
`repositoryAuthorityRevocationEpoch`. Then run:

```sh
python3 -B product.zip --root /repo --product apply \
  --archive product.zip --metadata product.json \
  --bootstrap-policy --maintenance
```

The runtime compiles the explicit document and adapters against the installed
charters and creates only the absent compiled snapshot. It refuses an existing
snapshot or comparison report, malformed or extra bootstrap inputs, and every
compiler rejection before writing. It never derives adapter capabilities,
grants authority, or creates comparison adjudications. A repository-specific
comparison report remains an adopter-owned follow-up sensor artifact; ordinary
product reconciliation accepts its absence and never rewrites one that exists.

Workflow-only releases continue to support the original lifecycle commands.
They intentionally fail product readiness. Final qualification must compare the
selected profile, exact workflow content digest, runtime descriptor, and outer
archive/metadata identities; rebuilding bytes requires fresh qualification.

## Reporting compatibility

Installed execution and review use the
[shared observation contract](../skills/execute-work/references/run-observation-contract.md)
through the generation's runtime. Both host exposures resolve the same workflow
and runtime bytes. Before activity or report capture, the producer checks
`runs capabilities --json` and separately checks the selected run version.
Unsupported reporting stays explicit and does not change the command outcome
or the run's version.

Local run reports belong to the adopter's git common-directory store, not the
immutable workflow generation or disposable reviewer scratch. Capture selected
output before cleanup and export it when retention must survive loss of that
store. Rolling back the product does not establish that the selected older
runtime understands newer observations; keep a compatible reader for retained
history. Release qualification must exercise these paths through the installed
artifact on both hosts, with actual result and candidate bindings.

## Releases

Pushing an annotated version tag publishes a release. The tag is `vMAJOR.MINOR.PATCH`,
optionally `-rc.N`, with no leading zeros. Major means an adopter must act, minor is
additive, and patch changes no contract. Release ids are `<profile>-<version>`.

```sh
git tag -a v1.2.3 -m "What an adopter needs to know" <merged-commit>
git push origin v1.2.3
```

Tag a commit CI ran on as the tip of a push to `main`, such as a merge commit. Any
other commit has no CI run to read, so it publishes nothing.

The annotation opens the release notes, ahead of the digest table and the generated
list of merged change requests. The release workflow:

- refuses a tag that is not annotated, not merged to `main`, or not strictly greater
  than every existing version tag, or whose commit does not equal or descend from the
  highest one's. Backports to an older line are unsupported;
- stops if the release already exists, because a release is never replaced;
- requires the Ubuntu job and `cut-release` to have succeeded in CI's run on the exact
  tagged commit, waiting a bounded time for either still in progress. A missing run, or
  a run where either failed or is missing, publishes nothing. CI's macOS job only
  informs: a run red, cancelled or still going because of it neither allows nor blocks a
  release. It can delay one only where a gating job is absent while it still runs:
  publishing cannot tell a job yet to appear from one that never will, so it waits its
  bound and then refuses;
- runs `scripts/cut-release.py` on the tagged tree, attests every output file, and
  publishes them all.

A final becomes latest. A candidate is a prerelease and never does.

CI on `main` informs and does not block a merge, because `main` is unprotected. A merge
can land on red, and it falls to whoever merges to read the result, because a red
Ubuntu job or `cut-release` on `main` blocks the next release.

Before tagging, whoever tags runs the full suite locally on macOS with Python 3.11, as
[`AGENTS.md`](../AGENTS.md) describes. The release job cannot verify it. Windows is not
validated.

Never move or reuse a release tag. A version tag that never published may be deleted to
clear the ordering guard, which is neither, and the refusal says so.

### Candidates and promotion

A runtime or record-shape change goes out first as a candidate, such as `v1.2.3-rc.1`,
and is applied in one adopter. Promote it by tagging the final version, `v1.2.3`, on the
same commit. The final is built from the same tree but under its own release id, so its
digests differ from the candidate's. An adopter verifies the final's own digests and
provenance, and `apply` treats it as a new generation.

### Cutting locally

`scripts/cut-release.py --version <version> --out <directory>` makes the same set
without publishing it: five archives, five release files and four records, or none of
them. It prints nothing on success, so read its exit code and the directory: 0 is
success, 1 a failed build, verification or qualifier, and 2 a refused version or output
directory. It owns that directory, removing earlier release files and refusing one that
holds anything else. A directory inside the checkout must be ignored.

A local suite run can leave bytecode under `skills/`, and `cut-release` then fails with
`release.source_validation: source corpus is invalid`. Clear it and cut again:

```sh
find skills -type d -name __pycache__ -prune -exec rm -rf {} +
```

Setting `PYTHONDONTWRITEBYTECODE=1` for the suite stops it being written.

### Where the stored runtime comes from

`product-runtime/` is the build output of `kwam1na/agent-delivery-harness`, now at
`v26-technologies/agent-delivery-harness`. It declares 0.8.6 and was built with
`npm run product:build-runtime` from a clean checkout of
`51a1d116c0715306a37de02410c4dee8249ec6d4`, that repository's `main` at the merge that the
`v0.8.6` tag names. 0.8.5 never published, and 0.8.6 differs from it only in tests and its
version. Over 0.8.4, a live rail provider that declares `independentOfScopedChecks` runs while the
gate's scoped checks run, starting once the gate's scoped pool is saturated. Its outcome is applied
where the serial loop reaches it, and under failFast the first scoped failure cancels it through
the rail. `cli-api.d.mts` gains only the optional per-attempt `signal` on `invokeProvider`.

0.8.4 loads Bun's FFI through a literal `import("bun:ffi")`, so an adopter's import scanner can bound
it, and the runtime build refuses any bundle that loads code through a computed specifier, other
than the CLI's one config loader.

0.8.3 added, over 0.8.2:

- background snapshot cleanup into a private per-user trash;
- the long-lived `git cat-file --batch` source reader, which verifies every frame;
- the per-process host-tool memo;
- the root-only, depth-bounded posture read;
- declared rail descriptors;
- the Bun libc group kill.

`HARNESS_VERSION` 0.8.6 is part of every preparation fingerprint, so it invalidates
receipts prepared by an earlier runtime: an adopter's in-flight deliveries prepare again
after applying it.

### Before the first version tag

`draft-building-v1` and earlier sets are the releases before the first version tag, and
latest resolves to `draft-building-v1` until a final is published. They carry no
attestation. `draft-building-v1` carries a 0.7.7 runtime and earlier sets that runtime or
older ones, none with the installed-data protocol, so an adopter whose installed runtime has
`installed-data-proof/1` must not apply them.

A runtime can have that protocol and still declare 0.7.7, because `runtimeVersion` does
not identify runtime bytes. Compare digests, or look for the protocol in the installed
`runtime/cli.mjs`.

Make the first version tag a candidate. The release workflow cannot be proven before a
real tag, and a final can then be promoted on the same commit. The first final's
annotation has to say that a host unable to start or message sessions now stops, and
brings that to the operator, rather than running lanes any other way.

## Declaring a delivery posture

A runtime from 0.7.8 on reads one file at the tip of the protected base,
`.agents/delivery-posture.json`, and `run-delivery`'s close-out reads the same file:

```json
{
  "schemaVersion": "delivery-posture/1",
  "posture": "focused",
  "reason": "The gate is still being optimized."
}
```

`schemaVersion`, `posture` (`gate` or `focused`) and a non-blank `reason` are required for
either posture, and any other member is ignored. A candidate changing the file gets nothing
until that change is on the base. Under `focused`, `verify`, `gate`, `record` and `resume`
each exit 0 with one outcome, `focused`, naming the posture, reason, source and base tip,
and they read and write no delivery record and run no obligation. `admit`, `check` and
`prepare` behave the same under either posture. Which checks a repository runs under
`focused` stays the repository's.

| What the base holds | Posture | Code |
| --- | --- | --- |
| A valid `focused` declaration | `focused` | none |
| A valid `gate` declaration | `gate` | none |
| No file at the tip | `gate` | `posture_undeclared` |
| Not valid UTF-8 JSON, JSON nesting arrays or objects more than 64 levels deep (the top-level object being 1), an object failing the shape above, or an unknown `posture` | `gate` | `posture_malformed` |
| A symlink, tree or submodule at that path, or a blob over 64 KiB | `gate` | `posture_malformed` |
| The base ref can't be resolved or read, or `git` fails or times out | `gate` | `posture_unreadable` |

The harness's commands also need `baseRef` to name `origin/<branch>`, with `origin` a
configured remote and `<branch>` its default branch. Anything else is `posture_unreadable`.
They print the same under `gate`, whatever the code, except that `posture_malformed` adds a
line on stderr. A repository with a configured `origin` now makes up to two network calls
per command, each bounded at 15 seconds, even if it declares nothing.

A script that chooses its own checks imports `resolveDeliveryPosture` from the runtime's
`kernel.mjs`. It takes `{ rootDir, baseRef }` and returns
`{ posture, reason, source, baseTipSha, code }`, with `source` the base as text, or
`fallback` where nothing was read, and `code` null when there is none. It never rejects.

Protection stops at the repository's own `.git/config`, and at the repository root.
A repository including a tracked file from that config has handed the candidate control of
git. Runs from a subdirectory that can still read `focused` are a known open gap.

### Moving Athena off its local version

Athena's `athena-delivery-posture/1` is not accepted: its adoption changes the file's
`schemaVersion`. Land that in two steps, because the base decides. A single commit changing the
file and the reader reads the old file from the base as `posture_malformed`, so its own push
falls back to the gate.

1. Update the runtime, make the local parser accept both schema versions, and change the file
   on the base.
2. Remove the module, as below.

Open branches still carrying the old parser read the new file as malformed, and are gated until
they rebase onto the first step.

These are the sites its local version touches, and what replaces each.

| Site | What replaces it |
| --- | --- |
| `.agents/delivery-posture.json` | Stays, with `schemaVersion` changed to `delivery-posture/1`. The other members need no change. |
| `scripts/delivery-posture.ts` and its test | Removed. `resolveDeliveryPosture` comes from the runtime's `kernel.mjs`. Its `describeDeliveryPosture` has no export: the outcome names the same facts. |
| `FOCUSED_PUSH_CHECKS`, `branchUpdatesOtherThanHead`, `runFocusedPushChecks` in that module | Move into `scripts/pre-push-review.ts`. They are the repository's own checks, so the product doesn't carry them. |
| The call in `scripts/pre-push-review.ts` | Imports the runtime's resolver, with `{ rootDir, baseRef: "origin/main" }`, and keeps choosing between its focused checks and `verify`. |
| The call in `scripts/pr-athena-delivery-run.ts` | Goes, and the entry has to return early on the focused outcome. Under `focused` the product's `gate` and `record` exit 0 and write nothing, so `runDeliverySequence` would otherwise fail at `git add` of the absent record. The real gate can't be run through the product while the base says `focused`. |
| The stdin capture in `.husky/pre-push` | Stays while `branchUpdatesOtherThanHead` needs the pushed refs. |
| The `delivery posture` arm of the `awk` filter in `.husky/pre-push` | Stays only if `pre-push-review.ts` keeps logging that line. |
| The boundary rule in `scripts/harness-blocker-inventory.ts` | Its `resolveDeliveryPosture` pattern still matches the import. Check that the rule still describes the script. |
| `scripts/coordinator-native-fixture.ts` and its count test | Drops the module, and the count falls by one. |
| `scripts/compound-solution-check.ts` | Drops the module from its list. |
| `scripts/harness-root-runtime-entry-guards.json` | Repinned, with the module gone. |
| The posture pin in `scripts/pr-athena-guidance-contract.test.ts` | Follows the new `AGENTS.md` text. |
| `AGENTS.md` and `docs/harness.md` | Name the product's posture rather than the local module. |

Two behaviours differ from Athena's reader. The product fetches into a private ref,
`refs/delivery/posture/<branch>`, and leaves `refs/remotes/origin/main` where it is, so a
script that relied on the read moving that ref fetches for itself. And offline, `focused`
needs that private ref, if there is one, and the tracking ref to agree.
