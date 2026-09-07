# Product generations

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
python3 -B product.zip --root /repo --product install --archive product.zip --metadata product.json --maintenance
python3 -B /repo/.agent-skills/current --root /repo harness check
python3 -B /repo/.agent-skills/current --root /repo --product status
python3 -B /repo/.agent-skills/current --root /repo --product rollback --maintenance
python3 -B /repo/.agent-skills/current --root /repo --product recover --maintenance
```

Use `update` with the next archive to advance a generation. Both host skill
exposures and the runtime resolve through the existing current generation.
The Node runtime can load erasable TypeScript consumer configuration without
consumer npm dependencies, including imports of the bundled kernel.

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
