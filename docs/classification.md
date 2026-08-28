# Classification boundary

The approved portable-workflow baseline is the source of classification evidence,
not a corpus to copy wholesale. `provenance.lock.json` records the source path,
immutable revision, output classification, modification status, output file
digest, licensing status, and owner for every cataloged file.

Classification and distribution status live only in `provenance.lock.json`.
The default profile contains only releasable portable-core entries. Bootstrap
is optional guidance, while the Linear profile remains empty until the optional
Linear adapter is extracted. Athena-derived source-only inventory remains
cataloged for traceability but cannot be selected until its tracker-neutral
adapter has been extracted. Athena-specific merge gates,
telemetry, deployment, reporting, generated-artifact policy, and domain rules
are deliberately absent from all selectable profile closures.
