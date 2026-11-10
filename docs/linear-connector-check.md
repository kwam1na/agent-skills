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

## Scheduled run

The same check also runs on a schedule, so a connector change is noticed without anyone
starting it. The scheduled run gates nothing either: no release waits for it, and a failure
reaches its owner without blocking anything.

It runs as a scheduled cloud agent, a routine on the owner's account, because it needs a
host with the Linear connector configured. Its parts are:

- **Schedule.** Weekly, Monday at 09:00 in the owner's time zone.
- **Where it works.** One disposable Linear team kept only for this check, with nothing
  else in it. Every issue the run creates is disposable.
- **Credentials.** The connector's own sign-in on the owner's account. They live with the
  connector, never the repository: no token, key or host payload is written to this
  repository or to the routine's prompt.
- **Failure route.** A failed run sends the owner a push notification and records the
  failure in the routine's own run result, which the owner reads within a day. It never
  reports through a Linear entity, since the connector may be what failed.
- **Cleanup.** The run ends by cancelling every issue it created, then searching for its
  marker and finding only canceled issues. The connector offers no issue delete, so
  leaving no entities behind means leaving no open entity behind. The cleanup runs after a
  failed step too, for whatever the run has created.

The routine's prompt:

```text
Run the Linear connector check described in docs/linear-connector-check.md of the
agent-skills repository, against the disposable Linear team <disposable team>, using
only the host's Linear connector.

1. Choose a random marker and search for it, expecting nothing.
2. Create a primary issue and a dependency issue in that team, each carrying the marker.
3. Read both, update one, and search for the marker again, expecting both.
4. Relate them, and read the relation back.
5. Reconcile: confirm each operation's result by reading it rather than trusting the
   response.
6. Cleanup, always, even after a failed step: cancel every issue this run created, then
   search for the marker and confirm every issue it finds is canceled.

If any step fails, the connector answers differently from the adapter's contract, or the
cleanup leaves an issue that is not canceled, send the owner a push notification naming
the step and what it saw, and end the run as failed. Report only through the
notification and this run's result, never through a Linear entity. Write nothing to the
repository, and record no tokens, keys, identifiers or host payloads.
```

The owner writes the disposable team's name in place of `<disposable team>` when
creating the routine. The routine is set up and changed by its owner, not by a release or
a delivery. Checking that its failure route works takes one deliberate failing run, with a
team name that does not exist in place of `<disposable team>`, whose notification reaches
the owner.
