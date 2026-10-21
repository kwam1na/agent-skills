# Product generations

For a skills-only pilot, install the **workflow-only core release** using the
[core install commands](../README.md#install-core-skills-without-a-harness).
That shape requires Python 3.11 or newer and Git, with directory-symlink support;
it does not require Node, the harness, or policy files. Omit `--product`.
The runtime and policy prerequisites below apply only to product generations.

A release may carry a `runtime/` payload alongside its selected portable workflow
profile. The existing release manifest covers both, and the lifecycle retains,
verifies, switches, and rolls them back as one generation. No separate runtime
pointer or maintenance journal is introduced.

Producer integration supplies `--runtime-directory` to `scripts/build-release.py
build`. That directory contains `runtime.json`, the CLI/kernel/policy JavaScript
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
