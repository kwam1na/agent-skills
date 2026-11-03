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

The annotation opens the release notes, ahead of the digest table and the generated
list of merged change requests. The release workflow:

- refuses a tag that is not annotated, not merged to `main`, or not strictly greater
  than every existing version tag, or whose commit does not equal or descend from the
  highest one's. Backports to an older line are unsupported;
- stops if the release already exists, because a release is never replaced;
- requires CI's success on the exact tagged commit, waiting a bounded time for a run
  still in progress. A failed, cancelled or missing run publishes nothing;
- runs `scripts/cut-release.py` on the tagged tree, attests every output file, and
  publishes them all.

A final becomes latest. A candidate is a prerelease and never does.

CI on `main` informs and does not block a merge, because `main` is unprotected. A merge
can land on red, and it falls to whoever merges to read the result, because a red
`main` blocks the next release.

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

### Before the first version tag

`draft-building-v1` and earlier sets are the releases before the first version tag, and
latest resolves to `draft-building-v1` until a final is published. They carry no
attestation. They also carry a 0.7.7 runtime without the installed-data protocol, so an
adopter whose installed runtime has `installed-data-proof/1` must not apply them.

The runtime in `product-runtime/` has that protocol and also declares 0.7.7, because
`runtimeVersion` does not identify runtime bytes. Compare digests, or look for the
protocol in the installed `runtime/cli.mjs`.

Make the first version tag a candidate. The release workflow cannot be proven before a
real tag, and a final can then be promoted on the same commit. The first final's
annotation has to say that a host unable to start or message sessions now stops, and
brings that to the operator, rather than running lanes any other way.
