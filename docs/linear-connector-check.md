# Linear connector check

Run this by hand when the host's Linear connector changes, or when Linear-facing skill
text changes, such as `skills/linear-tracker-adapter/` or a workflow that reaches the
tracker through it. Offline qualification proves the adapter's boundary, not that a live
connector works.

It gates nothing and retains no attestation. No release waits for it, and its result is
not evidence for one.

In a host with the connector configured, on clearly disposable work:

1. Choose a random marker and search for it, expecting nothing.
2. Create a primary issue and a dependency issue, each carrying the marker.
3. Read both, update one, and search for the marker again, expecting both.
4. Relate them, and read the relation back.
5. Reconcile: confirm each operation's result by reading it rather than trusting the
   response.
6. Cancel both issues.

If a step fails, or the connector answers differently from
`skills/linear-tracker-adapter/references/adapter-contract.json`, report it as a defect
against the adapter or the skill text that changed. Keep no host payloads, identifiers
or credentials from the run in this repository.
