# Classification boundary

The approved Athena V26-1413 baseline is the source of classification evidence,
not a corpus to copy wholesale. `provenance.lock.json` records the source path,
immutable revision, output classification, modification status, output file
digest, and licensing status for every shipped file.

The default profile contains only portable-core entries. Linear is an
optional-adapter profile, and bootstrap is optional guidance. Athena-specific
merge gates, telemetry, deployment, reporting, generated-artifact policy, and
domain rules are deliberately absent from both catalog and profile closures.
