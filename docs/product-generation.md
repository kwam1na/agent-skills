# Product generations

A release may carry a `runtime/` payload alongside its selected portable workflow
profile. The existing release manifest covers both, and the lifecycle retains,
verifies, switches, and rolls them back as one generation. No separate runtime
pointer or maintenance journal is introduced.

Producer integration supplies `--runtime-directory` to `scripts/build-release.py
build`. That directory contains `runtime.json`, the CLI/kernel/policy JavaScript
bundles, loader/bootstrap, and license notices. The descriptor's
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
adopter-owned, with its existing provenance and comparison adjudications.
Runtime commands share the existing lifecycle lock so maintenance cannot switch
the selected generation while a command executes.

Workflow-only releases continue to support the original lifecycle commands.
They intentionally fail product readiness. Final qualification must compare the
selected profile, exact workflow content digest, runtime descriptor, and outer
archive/metadata identities; rebuilding bytes requires fresh qualification.
